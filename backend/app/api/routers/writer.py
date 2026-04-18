# AIMETA P=写作API_章节生成和大纲创建|R=章节生成_大纲生成_评审_L2导演脚本_护栏检查|NR=不含数据存储|E=route:POST_/api/writer/*|X=http|A=生成_评审_过滤|D=fastapi,openai|S=net,db|RD=./README.ai
"""
Writer API Router - 人类化起点长篇写作系统

核心架构：
- L1 Planner：全知规划层（蓝图/大纲）
- L2 Director：章节导演脚本（ChapterMission）
- L3 Writer：有限视角正文生成

关键改进：
1. 信息可见性过滤：L3 Writer 只能看到已登场角色
2. 跨章 1234 逻辑：通过 ChapterMission 控制每章只写一个节拍
3. 后置护栏检查：自动检测并修复违规内容
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...core.config import settings
from ...core.dependencies import get_current_user
from ...db.session import AsyncSessionLocal, get_session
from ...models.foreshadowing import Foreshadowing, ForeshadowingStatusHistory
from ...models.novel import Chapter, ChapterOutline, ChapterVersion
from ...schemas.novel import (
    Chapter as ChapterSchema,
    ChapterGenerationStatus,
    AdvancedGenerateRequest,
    AdvancedGenerateResponse,
    DeleteChapterRequest,
    EditChapterRequest,
    ExpandOutlineRequest,
    EvaluateChapterRequest,
    FinalizeChapterRequest,
    FinalizeChapterResponse,
    FillMissingOutlineRequest,
    GenerateChapterRequest,
    GenerateOutlineRequest,
    NovelProject as NovelProjectSchema,
    SelectVersionRequest,
    UpdateChapterOutlineRequest,
)
from ...schemas.user import UserInDB

# chapter generation task registry: key = "{project_id}:{chapter_number}"
_generation_tasks: dict[str, asyncio.Task] = {}
from ...services.chapter_context_service import ChapterContextService
from ...services.chapter_ingest_service import ChapterIngestionService
from ...services.llm_service import LLMService
from ...services.novel_service import NovelService
from ...services.prompt_service import PromptService
from ...services.vector_store_service import VectorStoreService
from ...services.writer_context_builder import WriterContextBuilder
from ...services.chapter_guardrails import ChapterGuardrails
from ...services.ai_review_service import AIReviewService
from ...services.finalize_service import FinalizeService
from ...services.outline_generation_service import (
    DEFAULT_EXPAND_SUMMARY_MIN_LENGTH,
    DEFAULT_OUTLINE_BATCH_SIZE,
    OutlineGenerationService,
)
from ...utils.json_utils import remove_think_tags, unwrap_markdown_json
from ...repositories.system_config_repository import SystemConfigRepository
from ...services.pipeline_orchestrator import PipelineOrchestrator

router = APIRouter(prefix="/api/writer", tags=["Writer"])
logger = logging.getLogger(__name__)
# 使用固定 UTC+8，避免在 Windows/Python 环境缺少 tzdata 时 ZoneInfo 初始化失败。
CN_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_CHAPTER_TARGET_WORD_COUNT = 3000
MIN_CHAPTER_WORD_COUNT = 2200
WRITER_GENERATION_MAX_TOKENS = 7000


def _resolve_target_word_count(
    request_target: Optional[int],
    blueprint_chapter_length: Optional[int],
) -> int:
    if request_target and request_target > 0:
        return request_target
    if blueprint_chapter_length and blueprint_chapter_length > 0:
        return blueprint_chapter_length
    return DEFAULT_CHAPTER_TARGET_WORD_COUNT


def _calc_max_tokens(target_word_count: int) -> int:
    return min(max(target_word_count * 2, WRITER_GENERATION_MAX_TOKENS), 32000)
MIN_CHAPTER_VERSION_COUNT = 1
MAX_CHAPTER_VERSION_COUNT = 2
MAX_AUTO_FORESHADOWINGS_PER_CHAPTER = 5

_FORESHADOWING_RULES = [
    {
        "type": "mystery",
        "importance": "major",
        "confidence": 0.76,
        "keywords": ["神秘", "秘密", "真相", "谜团", "身份", "来历", "幕后", "蹊跷", "古怪", "诡异", "不对劲"],
    },
    {
        "type": "question",
        "importance": "major",
        "confidence": 0.72,
        "keywords": ["为什么", "为何", "到底", "究竟", "不明白", "不知道", "怎么会", "何以", "难道"],
    },
    {
        "type": "clue",
        "importance": "minor",
        "confidence": 0.64,
        "keywords": ["线索", "可疑", "异常", "不寻常", "暗示", "蛛丝马迹", "痕迹"],
    },
    {
        "type": "setup",
        "importance": "minor",
        "confidence": 0.61,
        "keywords": ["将来", "日后", "以后", "将会", "埋下", "伏笔", "悬念", "预感", "迟早", "终有一天"],
    },
]
_PAYOFF_MARKERS = ["原来", "真相", "答案", "揭晓", "揭开", "终于明白", "其实", "果然", "解释了", "应验"]
_REINFORCE_MARKERS = ["再次", "又", "仍", "依旧", "继续", "再度", "回想", "提到", "印证"]
_QUESTION_CUES = ["为什么", "为何", "到底", "究竟", "怎么会", "何以", "难道", "是谁", "是什么", "怎么", "吗"]
_TYPE_LIMITS = {"question": 2, "mystery": 2, "clue": 1, "setup": 1}
_MYSTERY_STRONG_CUES = {"秘密", "真相", "谜团", "身份", "来历", "幕后"}
_KEYWORD_STOPWORDS = {
    "这个", "那个", "一些", "一种", "已经", "还是", "就是", "如果", "但是", "因为",
    "他们", "我们", "你们", "自己", "事情", "时候", "没有", "不会", "不能", "然后",
    "以及", "为了", "这里", "那里", "这样", "那样", "非常", "特别", "可能", "突然",
}


def _clamp_version_count(value: int) -> int:
    return max(MIN_CHAPTER_VERSION_COUNT, min(MAX_CHAPTER_VERSION_COUNT, int(value)))


async def _load_project_schema(service: NovelService, project_id: str, user_id: int) -> NovelProjectSchema:
    return await service.get_project_schema(project_id, user_id)


def _extract_tail_excerpt(text: Optional[str], limit: int = 500) -> str:
    """截取章节结尾文本，默认保留 500 字。"""
    if not text:
        return ""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[-limit:]


def _normalize_snippet(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized.strip("，。！？!?；;：:、 ")


def _extract_keyword_anchors(text: str, max_count: int = 8) -> List[str]:
    """从中文文本中提取可用于匹配的锚词。"""
    anchors: List[str] = []
    seen = set()
    for token in re.findall(r"[\u4e00-\u9fff]{2,6}", text):
        if token in _KEYWORD_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        anchors.append(token)
        if len(anchors) >= max_count:
            break
    return anchors


def _build_auto_foreshadowing_name(content: str, foreshadowing_type: str) -> str:
    anchors = _extract_keyword_anchors(content, max_count=2)
    if anchors:
        return f"{foreshadowing_type}:{'·'.join(anchors)}"
    return f"{foreshadowing_type}:第1线索"


def _split_candidate_sentences(text: str) -> List[str]:
    """按句切分候选文本，避免同一段被多次窗口截断命中。"""
    raw_sentences = re.findall(r"[^。！？!?;\n]{6,120}[。！？!?;]?", text)
    sentences: List[str] = []
    for raw in raw_sentences:
        sentence = re.sub(r"\s+", " ", raw).strip()
        if 10 <= len(sentence) <= 90:
            sentences.append(sentence)
    return sentences


def _extract_foreshadowing_candidates(content: str) -> List[dict]:
    """
    从章节内容提取自动伏笔候选（精度优先规则）。
    返回字段：content/type/keywords/importance/confidence。
    """
    normalized_content = re.sub(r"\s+", " ", content or "").strip()
    if not normalized_content:
        return []

    candidates: List[dict] = []
    seen_snippets = set()
    type_counter = {key: 0 for key in _TYPE_LIMITS}
    sentences = _split_candidate_sentences(normalized_content)
    if not sentences:
        return []

    def add_candidate(snippet: str, foreshadowing_type: str, confidence: float, importance: str, keywords: List[str]) -> None:
        if type_counter.get(foreshadowing_type, 0) >= _TYPE_LIMITS.get(foreshadowing_type, 1):
            return
        normalized_snippet = _normalize_snippet(snippet)
        if len(normalized_snippet) < 10:
            return
        dedupe_key = normalized_snippet[:120]
        if dedupe_key in seen_snippets:
            return
        seen_snippets.add(dedupe_key)
        merged_keywords = keywords[:] if keywords else []
        if not merged_keywords:
            merged_keywords = _extract_keyword_anchors(normalized_snippet, max_count=6)
        candidates.append(
            {
                "content": normalized_snippet,
                "type": foreshadowing_type,
                "keywords": merged_keywords,
                "importance": importance,
                "confidence": confidence,
            }
        )
        type_counter[foreshadowing_type] = type_counter.get(foreshadowing_type, 0) + 1

    # 1) 问题型伏笔：有问句结构且包含关键疑问词
    for sentence in sentences:
        has_question_mark = ("？" in sentence or "?" in sentence)
        cue_hits = [kw for kw in _QUESTION_CUES if kw in sentence]
        if has_question_mark and cue_hits:
            add_candidate(sentence, "question", 0.74, "major", cue_hits[:3])
        if len(candidates) >= MAX_AUTO_FORESHADOWINGS_PER_CHAPTER:
            return candidates[:MAX_AUTO_FORESHADOWINGS_PER_CHAPTER]

    # 2) 神秘/线索/铺垫：按句匹配，不再整段窗口切割，减少重叠噪声
    for sentence in sentences:
        for rule in _FORESHADOWING_RULES:
            if rule["type"] == "question":
                continue
            matched_keywords = [keyword for keyword in rule["keywords"] if keyword in sentence]
            if not matched_keywords:
                continue
            # mystery 需要更强触发：
            # - 至少 1 个强线索词 + 至少 2 个命中词；或
            # - 问句结构 + 至少 1 个强线索词。
            if rule["type"] == "mystery":
                strong_hits = [kw for kw in matched_keywords if kw in _MYSTERY_STRONG_CUES]
                has_question_structure = ("？" in sentence or "?" in sentence) and any(cue in sentence for cue in _QUESTION_CUES)
                if not ((len(strong_hits) >= 1 and len(matched_keywords) >= 2) or (has_question_structure and len(strong_hits) >= 1)):
                    continue
            # clue/setup 至少要求 2 个锚词，或一个强触发词（线索/伏笔/悬念）
            if rule["type"] in ("clue", "setup"):
                strong_markers = {"线索", "伏笔", "悬念"}
                if len(matched_keywords) < 2 and not any(marker in matched_keywords for marker in strong_markers):
                    continue
            add_candidate(
                sentence,
                rule["type"],
                rule["confidence"],
                rule["importance"],
                matched_keywords[:4],
            )
            if len(candidates) >= MAX_AUTO_FORESHADOWINGS_PER_CHAPTER:
                return candidates[:MAX_AUTO_FORESHADOWINGS_PER_CHAPTER]

    return candidates[:MAX_AUTO_FORESHADOWINGS_PER_CHAPTER]


def _contains_any(text: str, needles: List[str]) -> bool:
    return any(needle and needle in text for needle in needles)


def _is_payoff_signal(content: str, anchors: List[str]) -> bool:
    if not _contains_any(content, _PAYOFF_MARKERS):
        return False
    if anchors and _contains_any(content, anchors):
        return True
    return False


def _is_reinforce_signal(content: str, anchors: List[str]) -> bool:
    if anchors and _contains_any(content, anchors):
        return True
    if _contains_any(content, _REINFORCE_MARKERS):
        return True
    return False


async def _sync_foreshadowings_for_chapter(
    session: AsyncSession,
    *,
    project_id: str,
    chapter: Chapter,
    content: str,
) -> dict:
    """
    将定稿章节内容同步到 foreshadowings 表：
    1) 重建当前章节的自动伏笔（is_manual=False）
    2) 推进历史活跃伏笔状态（planted/developing/partial -> developing/revealed）
    """
    normalized_content = (content or "").strip()

    # 每次重建当前章节的自动伏笔，避免重复与脏数据累积。
    await session.execute(
        delete(Foreshadowing).where(
            Foreshadowing.project_id == project_id,
            Foreshadowing.chapter_id == chapter.id,
            Foreshadowing.is_manual.is_(False),
        )
    )

    created_count = 0
    revealed_count = 0
    developing_count = 0

    if normalized_content:
        candidates = _extract_foreshadowing_candidates(normalized_content)
        reveal_offset_by_importance = {"major": 8, "minor": 4, "subtle": 12}
        for candidate in candidates:
            target_offset = reveal_offset_by_importance.get(candidate["importance"], 6)
            foreshadowing = Foreshadowing(
                project_id=project_id,
                chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,
                content=candidate["content"],
                type=candidate["type"],
                keywords=candidate["keywords"],
                status="planted",
                target_reveal_chapter=chapter.chapter_number + target_offset,
                name=_build_auto_foreshadowing_name(candidate["content"], candidate["type"]),
                importance=candidate["importance"],
                is_manual=False,
                ai_confidence=candidate["confidence"],
            )
            session.add(foreshadowing)
        created_count = len(candidates)

        # 推进历史活跃伏笔状态（不处理本章新建项）
        active_result = await session.execute(
            select(Foreshadowing).where(
                Foreshadowing.project_id == project_id,
                Foreshadowing.chapter_number < chapter.chapter_number,
                Foreshadowing.status.in_(["planted", "developing", "partial"]),
            )
        )
        active_foreshadowings = active_result.scalars().all()
        for fs in active_foreshadowings:
            anchors = [kw for kw in (fs.keywords or []) if isinstance(kw, str) and len(kw) >= 2]
            if not anchors:
                anchors = _extract_keyword_anchors(fs.content, max_count=6)

            if _is_payoff_signal(normalized_content, anchors):
                old_status = fs.status
                fs.status = "revealed"
                fs.resolved_chapter_id = chapter.id
                fs.resolved_chapter_number = chapter.chapter_number
                session.add(
                    ForeshadowingStatusHistory(
                        foreshadowing_id=fs.id,
                        old_status=old_status,
                        new_status="revealed",
                        chapter_number=chapter.chapter_number,
                        reason="章节文本出现回收信号并命中伏笔关键词",
                    )
                )
                revealed_count += 1
                continue

            if fs.status == "planted" and _is_reinforce_signal(normalized_content, anchors):
                fs.status = "developing"
                session.add(
                    ForeshadowingStatusHistory(
                        foreshadowing_id=fs.id,
                        old_status="planted",
                        new_status="developing",
                        chapter_number=chapter.chapter_number,
                        reason="章节文本再次提及伏笔关键词",
                    )
                )
                developing_count += 1

    await session.commit()
    return {
        "created": created_count,
        "revealed": revealed_count,
        "developing": developing_count,
    }


async def _sync_foreshadowings_after_finalize(
    project_id: str,
    chapter_number: int,
    content: str,
) -> None:
    """后台任务：在章节定稿/手改后同步伏笔表。"""
    async with AsyncSessionLocal() as session:
        try:
            chapter_result = await session.execute(
                select(Chapter).where(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number == chapter_number,
                )
            )
            chapter = chapter_result.scalars().first()
            if not chapter:
                logger.warning("伏笔同步跳过：章节不存在 project=%s chapter=%s", project_id, chapter_number)
                return

            stats = await _sync_foreshadowings_for_chapter(
                session,
                project_id=project_id,
                chapter=chapter,
                content=content,
            )
            logger.info(
                "伏笔同步完成 project=%s chapter=%s created=%s revealed=%s developing=%s",
                project_id,
                chapter_number,
                stats["created"],
                stats["revealed"],
                stats["developing"],
            )
        except Exception as exc:
            await session.rollback()
            logger.exception("伏笔同步失败 project=%s chapter=%s err=%s", project_id, chapter_number, exc)


async def _resolve_version_count(session: AsyncSession) -> int:
    """
    解析章节版本数量配置，优先级：
    1) SystemConfig: writer.chapter_versions
    2) SystemConfig: writer.version_count（兼容旧键）
    3) ENV: WRITER_CHAPTER_VERSION_COUNT / WRITER_CHAPTER_VERSIONS（与 config.py 对齐）
    4) ENV: WRITER_VERSION_COUNT（兼容旧）
    5) settings.writer_chapter_versions（默认=1）
    """
    repo = SystemConfigRepository(session)
    # 1) 新键优先，兼容旧键
    for key in ("writer.chapter_versions", "writer.version_count"):
        record = await repo.get_by_key(key)
        if record and record.value:
            try:
                val = int(record.value)
                if val >= 1:
                    return _clamp_version_count(val)
            except ValueError:
                pass
    # 2) 环境变量（与 Settings 对齐）
    for env in ("WRITER_CHAPTER_VERSION_COUNT", "WRITER_CHAPTER_VERSIONS", "WRITER_VERSION_COUNT"):
        v = os.getenv(env)
        if v:
            try:
                val = int(v)
                if val >= 1:
                    return _clamp_version_count(val)
            except ValueError:
                pass
    # 3) 默认值
    return _clamp_version_count(int(settings.writer_chapter_versions))


async def _generate_chapter_mission(
    llm_service: LLMService,
    prompt_service: PromptService,
    blueprint_dict: dict,
    previous_summary: str,
    previous_tail: str,
    outline_title: str,
    outline_summary: str,
    writing_notes: str,
    introduced_characters: List[str],
    all_characters: List[str],
    user_id: int,
    content_rating: Optional[str] = None,
) -> Optional[dict]:
    """
    L2 Director: 生成章节导演脚本（ChapterMission）
    """
    plan_prompt = await prompt_service.get_prompt("chapter_plan")
    if not plan_prompt:
        logger.warning("未配置 chapter_plan 提示词，跳过导演脚本生成")
        return None

    plan_input = f"""
[上一章摘要]
{previous_summary or "暂无（这是第一章）"}

[上一章结尾]
{previous_tail or "暂无（这是第一章）"}

[当前章节大纲]
标题：{outline_title}
摘要：{outline_summary}

[已登场角色]
{json.dumps(introduced_characters, ensure_ascii=False) if introduced_characters else "暂无"}

[全部角色]
{json.dumps(all_characters, ensure_ascii=False)}

[写作指令]
{writing_notes or "无额外指令"}
"""

    try:
        response = await llm_service.get_llm_response(
            system_prompt=plan_prompt,
            conversation_history=[{"role": "user", "content": plan_input}],
            temperature=0.3,
            user_id=user_id,
            role="writer",
            content_rating=content_rating,
            timeout=120.0,
        )
        cleaned = remove_think_tags(response)
        normalized = unwrap_markdown_json(cleaned)
        mission = json.loads(normalized)
        logger.info("成功生成章节导演脚本: macro_beat=%s", mission.get("macro_beat"))
        return mission
    except Exception as exc:
        logger.warning("生成章节导演脚本失败，将使用默认模式: %s", exc)
        return None


async def _rewrite_with_guardrails(
    llm_service: LLMService,
    prompt_service: PromptService,
    original_text: str,
    chapter_mission: Optional[dict],
    violations_text: str,
    user_id: int,
    content_rating: Optional[str] = None,
) -> str:
    """
    使用护栏修复提示词重写违规内容
    """
    rewrite_prompt = await prompt_service.get_prompt("rewrite_guardrails")
    if not rewrite_prompt:
        logger.warning("未配置 rewrite_guardrails 提示词，跳过自动修复")
        return original_text

    rewrite_input = f"""
[原文]
{original_text}

[章节导演脚本]
{json.dumps(chapter_mission, ensure_ascii=False, indent=2) if chapter_mission else "无"}

[违规列表]
{violations_text}
"""

    try:
        response = await llm_service.get_llm_response(
            system_prompt=rewrite_prompt,
            conversation_history=[{"role": "user", "content": rewrite_input}],
            temperature=0.3,
            user_id=user_id,
            role="writer",
            content_rating=content_rating,
            timeout=300.0,
            response_format=None,
            max_tokens=WRITER_GENERATION_MAX_TOKENS,
        )
        cleaned = remove_think_tags(response)
        logger.info("成功修复违规内容")
        return cleaned
    except Exception as exc:
        logger.warning("自动修复失败，返回原文: %s", exc)
        return original_text


async def _refresh_edit_summary_and_ingest(
    project_id: str,
    chapter_number: int,
    content: str,
    user_id: Optional[int],
) -> None:
    async with AsyncSessionLocal() as session:
        llm_service = LLMService(session)

        stmt = (
            select(Chapter)
            .options(selectinload(Chapter.selected_version))
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == chapter_number,
            )
        )
        result = await session.execute(stmt)
        chapter = result.scalars().first()
        if not chapter:
            return

        summary_text = None
        try:
            summary = await llm_service.get_summary(
                content,
                temperature=0.15,
                user_id=user_id,
            )
            summary_text = remove_think_tags(summary)
        except Exception as exc:
            logger.warning("编辑章节后自动生成摘要失败: %s", exc)

        if summary_text and chapter.selected_version and chapter.selected_version.content == content:
            chapter.real_summary = summary_text
            await session.commit()

        try:
            outline_stmt = select(ChapterOutline).where(
                ChapterOutline.project_id == project_id,
                ChapterOutline.chapter_number == chapter_number,
            )
            outline_result = await session.execute(outline_stmt)
            outline = outline_result.scalars().first()
            title = outline.title if outline and outline.title else f"第{chapter_number}章"
            ingest_service = ChapterIngestionService(llm_service=llm_service)
            await ingest_service.ingest_chapter(
                project_id=project_id,
                chapter_number=chapter_number,
                title=title,
                content=content,
                summary=None,
                user_id=user_id or 0,
            )
            logger.info("章节 %s 向量化入库成功", chapter_number)
        except Exception as exc:
            logger.error("章节 %s 向量化入库失败: %s", chapter_number, exc)


async def _finalize_chapter_async(
    project_id: str,
    chapter_number: int,
    selected_version_id: int,
    user_id: int,
    skip_vector_update: bool = False,
) -> None:
    async with AsyncSessionLocal() as session:
        llm_service = LLMService(session)

        stmt = (
            select(Chapter)
            .options(selectinload(Chapter.versions))
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == chapter_number,
            )
        )
        result = await session.execute(stmt)
        chapter = result.scalars().first()
        if not chapter:
            return

        selected_version = next(
            (v for v in chapter.versions if v.id == selected_version_id),
            None,
        )
        if not selected_version or not selected_version.content:
            return

        vector_store = None
        if settings.vector_store_enabled:
            try:
                vector_store = VectorStoreService()
            except RuntimeError as exc:
                logger.warning("向量库初始化失败，跳过定稿写入: %s", exc)

        finalize_service = FinalizeService(session, llm_service, vector_store)
        finalize_result = await finalize_service.finalize_chapter(
            project_id=project_id,
            chapter_number=chapter_number,
            chapter_text=selected_version.content,
            user_id=user_id,
            skip_vector_update=skip_vector_update,
        )
        if not finalize_result.get("success", False):
            chapter.selected_version_id = None
            chapter.status = ChapterGenerationStatus.WAITING_FOR_CONFIRM.value
            chapter.generation_progress = 100
            chapter.generation_step = "waiting_for_confirm"
            chapter.generation_step_index = 7
            chapter.generation_step_total = 7
            await session.commit()
            logger.error(
                "异步定稿失败 project=%s chapter=%s err=%s",
                project_id,
                chapter_number,
                finalize_result.get("error", "unknown"),
            )
            return

        chapter.selected_version_id = selected_version.id
        chapter.status = ChapterGenerationStatus.SUCCESSFUL.value
        chapter.generation_progress = 100
        chapter.generation_step = "completed"
        chapter.generation_step_index = 7
        chapter.generation_step_total = 7
        chapter.word_count = len(selected_version.content or "")
        await session.commit()

        try:
            stats = await _sync_foreshadowings_for_chapter(
                session,
                project_id=project_id,
                chapter=chapter,
                content=selected_version.content,
            )
            logger.info(
                "异步定稿伏笔同步完成 project=%s chapter=%s created=%s revealed=%s developing=%s",
                project_id,
                chapter_number,
                stats["created"],
                stats["revealed"],
                stats["developing"],
            )
        except Exception as exc:
            await session.rollback()
            logger.exception(
                "异步定稿伏笔同步失败 project=%s chapter=%s err=%s",
                project_id,
                chapter_number,
                exc,
            )


def _schedule_finalize_task(
    project_id: str,
    chapter_number: int,
    selected_version_id: int,
    user_id: int,
    skip_vector_update: bool = False,
) -> None:
    asyncio.create_task(
        _finalize_chapter_async(
            project_id=project_id,
            chapter_number=chapter_number,
            selected_version_id=selected_version_id,
            user_id=user_id,
            skip_vector_update=skip_vector_update,
        )
    )


@router.post("/advanced/generate", response_model=AdvancedGenerateResponse)
async def advanced_generate_chapter(
    request: AdvancedGenerateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> AdvancedGenerateResponse:
    """
    高级写作入口：通过 PipelineOrchestrator 统一编排生成流程。
    """
    orchestrator = PipelineOrchestrator(session)
    result = await orchestrator.generate_chapter(
        project_id=request.project_id,
        chapter_number=request.chapter_number,
        writing_notes=request.writing_notes,
        user_id=current_user.id,
        flow_config=request.flow_config.model_dump(),
    )

    flow_config = request.flow_config
    if flow_config.async_finalize and result.get("variants"):
        best_index = result.get("best_version_index", 0)
        variants = result["variants"]
        if 0 <= best_index < len(variants):
            selected_version_id = variants[best_index]["version_id"]
            background_tasks.add_task(
                _schedule_finalize_task,
                request.project_id,
                request.chapter_number,
                selected_version_id,
                current_user.id,
                False,
            )

    return AdvancedGenerateResponse(**result)


@router.post("/chapters/{chapter_number}/finalize", response_model=FinalizeChapterResponse)
async def finalize_chapter(
    chapter_number: int,
    request: FinalizeChapterRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> FinalizeChapterResponse:
    """
    定稿入口：选中版本后触发 FinalizeService 进行记忆更新与快照写入。
    """
    novel_service = NovelService(session)
    await novel_service.ensure_project_owner(request.project_id, current_user.id)

    stmt = (
        select(Chapter)
        .options(selectinload(Chapter.versions))
        .where(
            Chapter.project_id == request.project_id,
            Chapter.chapter_number == chapter_number,
        )
    )
    result = await session.execute(stmt)
    chapter = result.scalars().first()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    selected_version = next(
        (v for v in chapter.versions if v.id == request.selected_version_id),
        None,
    )
    if not selected_version or not selected_version.content:
        raise HTTPException(status_code=400, detail="选中的版本不存在或内容为空")

    vector_store = None
    if settings.vector_store_enabled and not request.skip_vector_update:
        try:
            vector_store = VectorStoreService()
        except RuntimeError as exc:
            logger.warning("向量库初始化失败，跳过定稿写入: %s", exc)

    finalize_service = FinalizeService(session, LLMService(session), vector_store)
    finalize_result = await finalize_service.finalize_chapter(
        project_id=request.project_id,
        chapter_number=chapter_number,
        chapter_text=selected_version.content,
        user_id=current_user.id,
        skip_vector_update=request.skip_vector_update or False,
    )
    if not finalize_result.get("success", False):
        chapter.selected_version_id = None
        chapter.status = ChapterGenerationStatus.WAITING_FOR_CONFIRM.value
        chapter.generation_progress = 100
        chapter.generation_step = "waiting_for_confirm"
        chapter.generation_step_index = 7
        chapter.generation_step_total = 7
        await session.commit()
        raise HTTPException(
            status_code=500,
            detail=finalize_result.get("error", "章节定稿失败"),
        )

    chapter.selected_version_id = selected_version.id
    chapter.status = ChapterGenerationStatus.SUCCESSFUL.value
    chapter.generation_progress = 100
    chapter.generation_step = "completed"
    chapter.generation_step_index = 7
    chapter.generation_step_total = 7
    chapter.word_count = len(selected_version.content or "")
    await session.commit()

    background_tasks.add_task(
        _sync_foreshadowings_after_finalize,
        request.project_id,
        chapter_number,
        selected_version.content,
    )

    return FinalizeChapterResponse(
        project_id=request.project_id,
        chapter_number=chapter_number,
        selected_version_id=selected_version.id,
        result=finalize_result,
    )


@router.post("/novels/{project_id}/chapters/generate", response_model=NovelProjectSchema)
async def generate_chapter(
    project_id: str,
    request: GenerateChapterRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    """
    生成章节正文 - 三层架构流程：
    1. 收集上下文和历史摘要
    2. L2 Director: 生成章节导演脚本（ChapterMission）
    3. 信息可见性过滤：裁剪蓝图，移除未登场角色
    4. L3 Writer: 生成正文（使用 writing_v2 提示词）
    5. 护栏检查：检测并修复违规内容
    """
    novel_service = NovelService(session)
    prompt_service = PromptService(session)
    llm_service = LLMService(session)
    context_builder = WriterContextBuilder()
    guardrails = ChapterGuardrails()

    project = await novel_service.ensure_project_owner(project_id, current_user.id)
    logger.info("用户 %s 开始为项目 %s 生成第 %s 章", current_user.id, project_id, request.chapter_number)
    outline = await novel_service.get_outline(project_id, request.chapter_number)
    if not outline:
        logger.warning("项目 %s 未找到第 %s 章纲要，生成流程终止", project_id, request.chapter_number)
        raise HTTPException(status_code=404, detail="蓝图中未找到对应章节纲要")

    chapter = await novel_service.get_or_create_chapter(project_id, request.chapter_number)
    generation_step_total = 7

    async def _update_generation_progress(
        *,
        progress: int,
        step: str,
        step_index: int,
        status: Optional[str] = None,
    ) -> None:
        chapter.generation_progress = max(0, min(100, int(progress)))
        chapter.generation_step = step
        chapter.generation_step_index = max(0, step_index)
        chapter.generation_step_total = generation_step_total
        if status:
            chapter.status = status
        await session.commit()

    async def _mark_generation_failed() -> None:
        """确保异常后章节状态统一收敛到 failed，避免前端刷新后长期卡在 generating。"""
        _generation_tasks.pop(f"{project_id}:{request.chapter_number}", None)
        try:
            await session.rollback()
        except Exception:
            pass

        try:
            chapter.status = "failed"
            chapter.generation_progress = 0
            chapter.generation_step = "failed"
            chapter.generation_step_index = 0
            chapter.generation_step_total = generation_step_total
            await session.commit()
        except Exception:
            logger.exception(
                "项目 %s 第 %s 章标记 failed 失败",
                project_id,
                request.chapter_number,
            )

    # register current asyncio task for cancellation support
    _gen_task_key = f"{project_id}:{request.chapter_number}"
    _cur_task = asyncio.current_task()
    if _cur_task:
        _generation_tasks[_gen_task_key] = _cur_task

    chapter.real_summary = None
    chapter.selected_version_id = None
    chapter.selected_version = None
    chapter.status = "generating"
    chapter.generation_started_at = datetime.now(CN_TIMEZONE)
    chapter.generation_progress = 3
    chapter.generation_step = "context_prep"
    chapter.generation_step_index = 1
    chapter.generation_step_total = generation_step_total
    await session.commit()

    outlines_map = {item.chapter_number: item for item in project.outlines}
    
    # ========== 1. 收集历史上下文 ==========
    completed_chapters = []
    completed_summaries = []
    latest_prev_number = -1
    previous_summary_text = ""
    previous_tail_excerpt = ""
    
    for existing in project.chapters:
        if existing.chapter_number >= request.chapter_number:
            continue
        if existing.selected_version is None or not existing.selected_version.content:
            continue
        if not existing.real_summary:
            try:
                summary = await llm_service.get_summary(
                    existing.selected_version.content,
                    temperature=0.15,
                    user_id=current_user.id,
                    timeout=180.0,
                )
            except HTTPException:
                await _mark_generation_failed()
                raise
            except Exception as exc:
                logger.exception(
                    "项目 %s 第 %s 章生成历史摘要失败: %s",
                    project_id,
                    request.chapter_number,
                    exc,
                )
                await _mark_generation_failed()
                raise HTTPException(status_code=500, detail="生成章节失败：历史摘要阶段异常") from exc
            existing.real_summary = remove_think_tags(summary)
            await session.commit()
        completed_chapters.append({
            "chapter_number": existing.chapter_number,
            "title": outlines_map.get(existing.chapter_number).title if outlines_map.get(existing.chapter_number) else f"第{existing.chapter_number}章",
            "summary": existing.real_summary,
        })
        completed_summaries.append(existing.real_summary or "")
        if existing.chapter_number > latest_prev_number:
            latest_prev_number = existing.chapter_number
            previous_summary_text = existing.real_summary or ""
            previous_tail_excerpt = _extract_tail_excerpt(existing.selected_version.content)

    await _update_generation_progress(progress=15, step="context_prep", step_index=1)

    try:
        # 这里只需要蓝图数据，避免依赖整项目序列化流程导致生成前失败。
        blueprint_schema = novel_service._build_blueprint_schema(project)
        blueprint_dict = blueprint_schema.model_dump()
    except HTTPException:
        await _mark_generation_failed()
        raise
    except Exception as exc:
        logger.exception(
            "项目 %s 第 %s 章构建蓝图上下文失败: %s",
            project_id,
            request.chapter_number,
            exc,
        )
        await _mark_generation_failed()
        raise HTTPException(status_code=500, detail="生成章节失败：加载项目信息异常") from exc

    # 处理关系字段名
    if "relationships" in blueprint_dict and blueprint_dict["relationships"]:
        for relation in blueprint_dict["relationships"]:
            if "character_from" in relation:
                relation["from"] = relation.pop("character_from")
            if "character_to" in relation:
                relation["to"] = relation.pop("character_to")

    target_word_count = _resolve_target_word_count(
        request_target=request.target_word_count,
        blueprint_chapter_length=blueprint_dict.get("chapter_length"),
    )
    content_rating = str(blueprint_dict.get("content_rating") or "safe").lower()
    min_word_count = int(target_word_count * 0.73)
    max_tokens = _calc_max_tokens(target_word_count)

    outline_title = outline.title or f"第{outline.chapter_number}章"
    outline_summary = outline.summary or "暂无摘要"
    writing_notes = request.writing_notes or "无额外写作指令"

    # 提取所有角色名
    all_characters = [c.get("name") for c in blueprint_dict.get("characters", []) if c.get("name")]

    # ========== 2. L2 Director: 生成章节导演脚本 ==========
    await _update_generation_progress(progress=22, step="director_mission", step_index=2)
    try:
        chapter_mission = await _generate_chapter_mission(
            llm_service=llm_service,
            prompt_service=prompt_service,
            blueprint_dict=blueprint_dict,
            previous_summary=previous_summary_text,
            previous_tail=previous_tail_excerpt,
            outline_title=outline_title,
            outline_summary=outline_summary,
            writing_notes=writing_notes,
            introduced_characters=[],  # 将在下一步填充
            all_characters=all_characters,
            user_id=current_user.id,
            content_rating=content_rating,
        )
    except HTTPException:
        await _mark_generation_failed()
        raise
    except Exception as exc:
        logger.exception(
            "项目 %s 第 %s 章生成导演脚本失败: %s",
            project_id,
            request.chapter_number,
            exc,
        )
        await _mark_generation_failed()
        raise HTTPException(status_code=500, detail="生成章节失败：导演脚本阶段异常") from exc

    # 从导演脚本中提取允许登场的新角色
    allowed_new_characters = []
    if chapter_mission:
        allowed_new_characters = chapter_mission.get("allowed_new_characters", [])

    # ========== 3. 信息可见性过滤 ==========
    try:
        visibility_context = context_builder.build_visibility_context(
            blueprint=blueprint_dict,
            completed_summaries=completed_summaries,
            previous_tail=previous_tail_excerpt,
            outline_title=outline_title,
            outline_summary=outline_summary,
            writing_notes=writing_notes,
            allowed_new_characters=allowed_new_characters,
        )
    except Exception as exc:
        logger.exception(
            "项目 %s 第 %s 章可见性上下文构建失败: %s",
            project_id,
            request.chapter_number,
            exc,
        )
        await _mark_generation_failed()
        raise HTTPException(status_code=500, detail="生成章节失败：上下文构建异常") from exc

    writer_blueprint = visibility_context["writer_blueprint"]
    forbidden_characters = visibility_context["forbidden_characters"]
    introduced_characters = visibility_context["introduced_characters"]

    logger.info(
        "项目 %s 第 %s 章信息可见性: 已登场=%s, 允许新登场=%s, 禁止=%s",
        project_id,
        request.chapter_number,
        len(introduced_characters),
        len(allowed_new_characters),
        len(forbidden_characters),
    )

    # ========== 4. 准备 RAG 上下文 ==========
    await _update_generation_progress(progress=38, step="rag_retrieval", step_index=3)
    vector_store: Optional[VectorStoreService]
    if not settings.vector_store_enabled:
        vector_store = None
    else:
        try:
            vector_store = VectorStoreService()
        except RuntimeError as exc:
            logger.warning("向量库初始化失败，RAG 检索被禁用: %s", exc)
            vector_store = None
    context_service = ChapterContextService(llm_service=llm_service, vector_store=vector_store)

    query_parts = [outline_title, outline_summary]
    if request.writing_notes:
        query_parts.append(request.writing_notes)
    rag_query = "\n".join(part for part in query_parts if part)
    try:
        rag_context = await context_service.retrieve_for_generation(
            project_id=project_id,
            query_text=rag_query or outline.title or outline.summary or "",
            user_id=current_user.id,
        )
    except HTTPException:
        await _mark_generation_failed()
        raise
    except Exception as exc:
        logger.exception(
            "项目 %s 第 %s 章 RAG 检索失败: %s",
            project_id,
            request.chapter_number,
            exc,
        )
        await _mark_generation_failed()
        raise HTTPException(status_code=500, detail="生成章节失败：检索上下文阶段异常") from exc
    await _update_generation_progress(progress=48, step="rag_retrieval", step_index=3)
    rag_chunks_text = "\n\n".join(rag_context.chunk_texts()) if rag_context.chunks else "未检索到章节片段"
    rag_summaries_text = "\n".join(rag_context.summary_lines()) if rag_context.summaries else "未检索到章节摘要"

    # ========== 5. 构建写作提示词 ==========
    # 优先使用 writing_v2，fallback 到 writing
    try:
        writer_prompt = await prompt_service.get_prompt("writing_v2")
        if not writer_prompt:
            writer_prompt = await prompt_service.get_prompt("writing")
    except Exception as exc:
        logger.exception(
            "项目 %s 第 %s 章加载写作提示词失败: %s",
            project_id,
            request.chapter_number,
            exc,
        )
        await _mark_generation_failed()
        raise HTTPException(status_code=500, detail="生成章节失败：加载写作提示词异常") from exc
    if not writer_prompt:
        logger.error("未配置写作提示词，无法生成章节内容")
        chapter.status = "failed"
        chapter.generation_progress = 0
        chapter.generation_step = "failed"
        chapter.generation_step_index = 0
        chapter.generation_step_total = generation_step_total
        await session.commit()
        raise HTTPException(status_code=500, detail="缺少写作提示词，请联系管理员配置")

    # 使用裁剪后的蓝图（移除了 full_synopsis 和未登场角色）
    blueprint_text = json.dumps(writer_blueprint, ensure_ascii=False, indent=2)
    
    # 构建导演脚本文本
    mission_text = json.dumps(chapter_mission, ensure_ascii=False, indent=2) if chapter_mission else "无导演脚本"
    
    # 构建禁止角色列表
    forbidden_text = json.dumps(forbidden_characters, ensure_ascii=False) if forbidden_characters else "无"

    prompt_sections = [
        ("[世界蓝图](JSON，已裁剪)", blueprint_text),
        ("[上一章摘要]", previous_summary_text or "暂无（这是第一章）"),
        ("[上一章结尾]", previous_tail_excerpt or "暂无（这是第一章）"),
        ("[章节导演脚本](JSON)", mission_text),
        ("[检索到的剧情上下文](Markdown)", rag_chunks_text),
        ("[检索到的章节摘要](Markdown)", rag_summaries_text),
        ("[当前章节目标]", f"标题：{outline_title}\n摘要：{outline_summary}\n写作要求：{writing_notes}"),
        (
            "[篇幅与排版要求]",
            (
                f"目标字数：约 {target_word_count} 字，"
                f"不得少于 {min_word_count} 字。"
                "段落清晰，尽量保持自然段首行空两格。"
            ),
        ),
        ("[禁止角色](本章不允许提及)", forbidden_text),
    ]
    prompt_input = "\n\n".join(f"{title}\n{content}" for title, content in prompt_sections if content)
    logger.debug("章节写作提示词长度: %s 字符", len(prompt_input))
    await _update_generation_progress(progress=55, step="draft_generation", step_index=4)

    # ========== 6. L3 Writer: 生成正文 ==========
    async def _generate_single_version(idx: int, version_style_hint: Optional[str] = None) -> Dict:
        """生成单个版本，支持差异化风格提示"""
        try:
            # 如果有版本风格提示，添加到 prompt_input
            final_prompt_input = prompt_input
            if version_style_hint:
                final_prompt_input += f"\n\n[版本风格提示]\n{version_style_hint}"

            response = await llm_service.get_llm_response(
                system_prompt=writer_prompt,
                conversation_history=[{"role": "user", "content": final_prompt_input}],
                temperature=0.9,
                user_id=current_user.id,
                role="writer",
                content_rating=content_rating,
                timeout=600.0,
                response_format=None,
                max_tokens=max_tokens,
            )
            cleaned = remove_think_tags(response)
            normalized = unwrap_markdown_json(cleaned)
            
            # ========== 7. 护栏检查 ==========
            guardrail_result = guardrails.check(
                generated_text=normalized,
                forbidden_characters=forbidden_characters,
                allowed_new_characters=allowed_new_characters,
                pov=chapter_mission.get("pov") if chapter_mission else None,
            )

            final_content = normalized
            guardrail_metadata = {"passed": guardrail_result.passed, "violations": []}

            if not guardrail_result.passed:
                logger.warning(
                    "项目 %s 第 %s 章版本 %s 检测到 %s 个违规",
                    project_id,
                    request.chapter_number,
                    idx + 1,
                    len(guardrail_result.violations),
                )
                guardrail_metadata["violations"] = [
                    {"type": v.type, "severity": v.severity, "description": v.description}
                    for v in guardrail_result.violations
                ]

                # 尝试自动修复
                violations_text = guardrails.format_violations_for_rewrite(guardrail_result)
                final_content = await _rewrite_with_guardrails(
                    llm_service=llm_service,
                    prompt_service=prompt_service,
                    original_text=normalized,
                    chapter_mission=chapter_mission,
                    violations_text=violations_text,
                    user_id=current_user.id,
                    content_rating=content_rating,
                )
                if not final_content:
                    logger.warning(
                        "项目 %s 第 %s 章版本 %s 自动修复返回空内容，回退原始正文",
                        project_id,
                        request.chapter_number,
                        idx + 1,
                    )
                    final_content = normalized

            def _extract_text(value: object) -> Optional[str]:
                if not value:
                    return None
                if isinstance(value, str):
                    return value
                if isinstance(value, dict):
                    for key in (
                        "content",
                        "chapter_content",
                        "chapter_text",
                        "text",
                        "body",
                        "story",
                        "chapter",
                        "full_content",
                        "optimized_content",
                    ):
                        if value.get(key):
                            nested = _extract_text(value.get(key))
                            if nested:
                                return nested
                    return None
                if isinstance(value, list):
                    for item in value:
                        nested = _extract_text(item)
                        if nested:
                            return nested
                return None

            parsed_json = None
            extracted_text = None
            try:
                parsed_json = json.loads(final_content)
                extracted_text = _extract_text(parsed_json)
            except Exception:
                parsed_json = None

            resolved_content: Optional[str] = extracted_text
            if not resolved_content and isinstance(final_content, str):
                stripped = final_content.strip()
                if stripped:
                    looks_like_json = (
                        (stripped.startswith("{") and stripped.endswith("}"))
                        or (stripped.startswith("[") and stripped.endswith("]"))
                    )
                    # 若是 JSON 且无法提取正文，视为无效输出；避免把结构化包装写入正文。
                    if not looks_like_json or parsed_json is None:
                        resolved_content = stripped

            if not resolved_content:
                logger.error(
                    "项目 %s 第 %s 章版本 %s 未提取到正文: final_preview=%s",
                    project_id,
                    request.chapter_number,
                    idx + 1,
                    (final_content or "")[:400],
                )

            return {
                "content": resolved_content,
                "parsed_json": parsed_json,
                "guardrail": guardrail_metadata,
                "chapter_mission": chapter_mission,
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "项目 %s 生成第 %s 章第 %s 个版本时发生异常: %s",
                project_id,
                request.chapter_number,
                idx + 1,
                exc,
            )
            raise HTTPException(
                status_code=500,
                detail=f"生成章节第 {idx + 1} 个版本时失败: {str(exc)[:200]}"
            )

    version_count = await _resolve_version_count(session)
    logger.info(
        "项目 %s 第 %s 章计划生成 %s 个版本",
        project_id,
        request.chapter_number,
        version_count,
    )

    # 版本差异化风格提示
    version_style_hints = [
        "情绪更细腻，节奏更慢，多写内心戏和感官描写",
        "冲突更强，节奏更快，多写动作和对话",
        "悬念更重，多埋伏笔，结尾钩子更强",
    ]

    try:
        await _update_generation_progress(progress=55, step="draft_generation", step_index=4)
        raw_versions = []
        for idx in range(version_count):
            result = await _generate_single_version(
                idx, version_style_hints[idx] if idx < len(version_style_hints) else None
            )
            raw_versions.append(result)
    except Exception as exc:
        logger.exception("项目 %s 生成第 %s 章时发生异常: %s", project_id, request.chapter_number, exc)
        chapter.status = "failed"
        chapter.generation_progress = 0
        chapter.generation_step = "failed"
        chapter.generation_step_index = 0
        chapter.generation_step_total = generation_step_total
        await session.commit()
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(
            status_code=500,
            detail=f"生成章节失败: {str(exc)[:200]}"
        )

    await _update_generation_progress(progress=82, step="draft_generation", step_index=4)

    contents: List[str] = []
    metadata: List[Dict] = []
    for version_idx, variant in enumerate(raw_versions, start=1):
        if isinstance(variant, dict):
            candidate = variant.get("content")
            if isinstance(candidate, str):
                candidate = candidate.strip()
            if not candidate and "chapter_content" in variant and variant["chapter_content"]:
                candidate = str(variant["chapter_content"]).strip()
            if not candidate:
                logger.error(
                    "项目 %s 第 %s 章版本 %s 生成结果缺少正文内容，metadata_keys=%s",
                    project_id,
                    request.chapter_number,
                    version_idx,
                    list(variant.keys()),
                )
                chapter.status = "failed"
                chapter.generation_progress = 0
                chapter.generation_step = "failed"
                chapter.generation_step_index = 0
                chapter.generation_step_total = generation_step_total
                await session.commit()
                raise HTTPException(
                    status_code=502,
                    detail=f"生成章节第 {version_idx} 个版本失败：模型未返回有效正文，请重试。",
                )
            contents.append(candidate)
            metadata.append(variant)
        else:
            text = str(variant).strip()
            if not text:
                chapter.status = "failed"
                chapter.generation_progress = 0
                chapter.generation_step = "failed"
                chapter.generation_step_index = 0
                chapter.generation_step_total = generation_step_total
                await session.commit()
                raise HTTPException(
                    status_code=502,
                    detail=f"生成章节第 {version_idx} 个版本失败：模型返回空内容，请重试。",
                )
            contents.append(text)
            metadata.append({"raw": variant})

    # ========== 8. AI Review: 自动评审多版本 ==========
    await _update_generation_progress(progress=86, step="quality_review", step_index=5)
    ai_review_result = None
    if len(contents) > 1:
        try:
            ai_review_service = AIReviewService(llm_service, prompt_service)
            ai_review_result = await ai_review_service.review_versions(
                versions=contents,
                chapter_mission=chapter_mission,
                user_id=current_user.id,
                content_rating=content_rating,
            )
            if ai_review_result:
                logger.info(
                    "项目 %s 第 %s 章 AI 评审完成: 推荐版本=%s",
                    project_id,
                    request.chapter_number,
                    ai_review_result.best_version_index,
                )
                # 将评审结果附加到 metadata
                for i, m in enumerate(metadata):
                    m["ai_review"] = {
                        "is_best": i == ai_review_result.best_version_index,
                        "scores": ai_review_result.scores,
                        "evaluation": ai_review_result.overall_evaluation if i == ai_review_result.best_version_index else None,
                        "flaws": ai_review_result.critical_flaws if i == ai_review_result.best_version_index else None,
                        "suggestions": ai_review_result.refinement_suggestions if i == ai_review_result.best_version_index else None,
                    }
        except Exception as exc:
            logger.warning("AI 评审失败，跳过: %s", exc)

    await _update_generation_progress(progress=96, step="persist_versions", step_index=6)
    try:
        await novel_service.replace_chapter_versions(chapter, contents, metadata)
    except Exception as exc:
        logger.exception("项目 %s 第 %s 章写入版本失败: %s", project_id, request.chapter_number, exc)
        chapter.status = "failed"
        chapter.generation_progress = 0
        chapter.generation_step = "failed"
        chapter.generation_step_index = 0
        chapter.generation_step_total = generation_step_total
        await session.commit()
        raise HTTPException(status_code=500, detail="章节版本写入失败，请重试。")
    logger.info(
        "项目 %s 第 %s 章生成完成，已写入 %s 个版本",
        project_id,
        request.chapter_number,
        len(contents),
    )
    try:
        return await _load_project_schema(novel_service, project_id, current_user.id)
    except HTTPException:
        await _mark_generation_failed()
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(
            "项目 %s 第 %s 章生成后加载项目状态失败: %s",
            project_id,
            request.chapter_number,
            exc,
        )
        await _mark_generation_failed()
        raise HTTPException(status_code=500, detail="章节已生成，但刷新项目状态失败，请重试") from exc
    finally:
        _generation_tasks.pop(_gen_task_key, None)


@router.post("/novels/{project_id}/chapters/{chapter_number}/cancel-generation")
async def cancel_chapter_generation(
    project_id: str,
    chapter_number: int,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> dict:
    novel_service = NovelService(session)
    await novel_service.ensure_project_owner(project_id, current_user.id)

    key = f"{project_id}:{chapter_number}"
    task = _generation_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()

    chapter = await novel_service.get_or_create_chapter(project_id, chapter_number)
    if chapter.status in ("generating", "evaluating", "selecting"):
        chapter.status = "failed"
        chapter.generation_progress = 0
        chapter.generation_step = "cancelled"
        chapter.generation_step_index = 0
        await session.commit()

    return {"cancelled": True}


@router.post("/novels/{project_id}/chapters/select", response_model=NovelProjectSchema)
async def select_chapter_version(
    project_id: str,
    request: SelectVersionRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    project = await novel_service.ensure_project_owner(project_id, current_user.id)
    chapter = await novel_service.get_or_create_chapter(project_id, request.chapter_number)

    chapter.status = ChapterGenerationStatus.SELECTING.value
    chapter.generation_progress = 95
    chapter.generation_step = "selecting_version"
    chapter.generation_step_index = 6
    chapter.generation_step_total = 7
    await session.commit()

    # 使用 novel_service.select_chapter_version 确保排序一致
    # 该函数会按 created_at 排序并校验索引
    try:
        selected_version = await novel_service.select_chapter_version(chapter, request.version_index)
    except HTTPException:
        chapter.status = ChapterGenerationStatus.WAITING_FOR_CONFIRM.value
        chapter.generation_progress = 100
        chapter.generation_step = "waiting_for_confirm"
        chapter.generation_step_index = 7
        chapter.generation_step_total = 7
        await session.commit()
        raise
    
    # 校验内容是否为空
    if not selected_version.content or len(selected_version.content.strip()) == 0:
        # 回滚状态，不标记为 successful
        await session.rollback()
        raise HTTPException(status_code=400, detail="选中的版本内容为空，无法确认为最终版")

    # 异步触发向量化入库
    try:
        llm_service = LLMService(session)
        ingest_service = ChapterIngestionService(llm_service=llm_service)
        outline_stmt = select(ChapterOutline).where(
            ChapterOutline.project_id == project_id,
            ChapterOutline.chapter_number == request.chapter_number,
        )
        outline_result = await session.execute(outline_stmt)
        outline = outline_result.scalars().first()
        chapter_title = outline.title if outline and outline.title else f"第{request.chapter_number}章"
        await ingest_service.ingest_chapter(
            project_id=project_id,
            chapter_number=request.chapter_number,
            title=chapter_title,
            content=selected_version.content,
            summary=None,
            user_id=current_user.id,
        )
        logger.info(f"章节 {request.chapter_number} 向量化入库成功")
    except Exception as e:
        logger.error(f"章节 {request.chapter_number} 向量化入库失败: {e}")
        # 向量化失败不应阻止版本选择，仅记录错误
    background_tasks.add_task(
        _sync_foreshadowings_after_finalize,
        project_id,
        request.chapter_number,
        selected_version.content,
    )

    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/evaluate", response_model=NovelProjectSchema)
async def evaluate_chapter(
    project_id: str,
    request: EvaluateChapterRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    prompt_service = PromptService(session)
    llm_service = LLMService(session)

    project = await novel_service.ensure_project_owner(project_id, current_user.id)
    # 确保预加载 selected_version 关系
    from sqlalchemy.orm import selectinload
    stmt = (
        select(Chapter)
        .options(selectinload(Chapter.selected_version))
        .where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == request.chapter_number,
        )
    )
    result = await session.execute(stmt)
    chapter = result.scalars().first()
    
    if not chapter:
        chapter = await novel_service.get_or_create_chapter(project_id, request.chapter_number)

    # 如果没有选中版本，使用最新版本进行评审
    version_to_evaluate = chapter.selected_version
    if not version_to_evaluate:
        # 获取该章节的所有版本，选择最新的一个
        from sqlalchemy.orm import selectinload
        stmt_versions = (
            select(Chapter)
            .options(selectinload(Chapter.versions))
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == request.chapter_number,
            )
        )
        result_versions = await session.execute(stmt_versions)
        chapter_with_versions = result_versions.scalars().first()
        
        if not chapter_with_versions or not chapter_with_versions.versions:
            raise HTTPException(status_code=400, detail="该章节还没有生成任何版本，无法进行评审")
        
        # 使用最新的版本（列表中的最后一个）
        version_to_evaluate = chapter_with_versions.versions[-1]
    
    if not version_to_evaluate or not version_to_evaluate.content:
        raise HTTPException(status_code=400, detail="版本内容为空，无法进行评审")

    chapter.status = "evaluating"
    chapter.generation_progress = 84
    chapter.generation_step = "evaluating"
    chapter.generation_step_index = 2
    chapter.generation_step_total = 3
    await session.commit()

    eval_prompt = await prompt_service.get_prompt("evaluation")
    if not eval_prompt:
        logger.warning("未配置名为 'evaluation' 的评审提示词，将跳过 AI 评审")
        # 使用 add_chapter_evaluation 创建评审记录
        await novel_service.add_chapter_evaluation(
            chapter=chapter,
            version=version_to_evaluate,
            feedback="未配置评审提示词",
            decision="skipped"
        )
        return await _load_project_schema(novel_service, project_id, current_user.id)

    try:
        content_rating = str(getattr(project.blueprint, "content_rating", "safe") or "safe").lower()
        evaluation_raw = await llm_service.get_llm_response(
            system_prompt=eval_prompt,
            conversation_history=[{"role": "user", "content": version_to_evaluate.content}],
            temperature=0.3,
            user_id=current_user.id,
            role="reviewer",
            content_rating=content_rating,
        )
        evaluation_text = remove_think_tags(evaluation_raw)
        
        # 校验 AI 返回的内容不为空
        if not evaluation_text or len(evaluation_text.strip()) == 0:
            raise ValueError("评审结果为空")
        
        # 使用 add_chapter_evaluation 创建评审记录
        # 这会自动设置状态为 WAITING_FOR_CONFIRM
        await novel_service.add_chapter_evaluation(
            chapter=chapter,
            version=version_to_evaluate,
            feedback=evaluation_text,
            decision="reviewed"
        )
        logger.info("项目 %s 第 %s 章评审成功", project_id, request.chapter_number)
    except Exception as exc:
        logger.exception("项目 %s 第 %s 章评审失败: %s", project_id, request.chapter_number, exc)
        # 回滚事务，恢复状态
        await session.rollback()
        
        # 重新加载 chapter 对象（因为 rollback 后对象已脱离 session）
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == request.chapter_number,
            )
        )
        result = await session.execute(stmt)
        chapter = result.scalars().first()
        
        if chapter:
            # 使用 add_chapter_evaluation 创建失败记录
            # 注意：这里不能再用 add_chapter_evaluation，因为它会设置状态为 waiting_for_confirm
            # 失败时应该设置为 evaluation_failed
            from app.models.novel import ChapterEvaluation
            evaluation_record = ChapterEvaluation(
                chapter_id=chapter.id,
                version_id=version_to_evaluate.id,
                decision="failed",
                feedback=f"评审失败: {str(exc)}",
                score=None
            )
            session.add(evaluation_record)
            chapter.status = "evaluation_failed"
            chapter.generation_progress = 0
            chapter.generation_step = "evaluation_failed"
            chapter.generation_step_index = 0
            chapter.generation_step_total = 3
            await session.commit()
        
        # 抛出异常，让前端知道评审失败
        raise HTTPException(status_code=500, detail=f"评审失败: {str(exc)}")
    
    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/update-outline", response_model=NovelProjectSchema)
async def update_chapter_outline(
    project_id: str,
    request: UpdateChapterOutlineRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    await novel_service.ensure_project_owner(project_id, current_user.id)

    outline = await novel_service.get_outline(project_id, request.chapter_number)
    if not outline:
        raise HTTPException(status_code=404, detail="未找到对应章节大纲")

    outline.title = request.title
    outline.summary = request.summary
    await session.commit()

    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/delete", response_model=NovelProjectSchema)
async def delete_chapters(
    project_id: str,
    request: DeleteChapterRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    await novel_service.ensure_project_owner(project_id, current_user.id)

    for ch_num in request.chapter_numbers:
        await novel_service.delete_chapter(project_id, ch_num)

    await session.commit()
    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/outline", response_model=NovelProjectSchema)
async def generate_chapters_outline(
    project_id: str,
    request: GenerateOutlineRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    prompt_service = PromptService(session)
    llm_service = LLMService(session)
    await novel_service.ensure_project_owner(project_id, current_user.id)

    outline_service = OutlineGenerationService(
        session=session,
        prompt_service=prompt_service,
        llm_service=llm_service,
        novel_service=novel_service,
    )
    try:
        await outline_service.generate_and_persist_range(
            project_id=project_id,
            user_id=current_user.id,
            start_chapter=request.start_chapter,
            num_chapters=request.num_chapters,
            batch_size=DEFAULT_OUTLINE_BATCH_SIZE,
        )
    except Exception as exc:
        logger.exception("生成大纲失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"大纲生成失败: {str(exc)}")

    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/outline/fill-missing", response_model=NovelProjectSchema)
async def fill_missing_chapters_outline(
    project_id: str,
    request: FillMissingOutlineRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    prompt_service = PromptService(session)
    llm_service = LLMService(session)

    await novel_service.ensure_project_owner(project_id, current_user.id)
    outline_service = OutlineGenerationService(
        session=session,
        prompt_service=prompt_service,
        llm_service=llm_service,
        novel_service=novel_service,
    )
    try:
        await outline_service.fill_missing_outlines(
            project_id=project_id,
            user_id=current_user.id,
            batch_size=request.batch_size or DEFAULT_OUTLINE_BATCH_SIZE,
        )
    except Exception as exc:
        logger.exception("补齐缺失大纲失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"补齐缺失大纲失败: {str(exc)}")

    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/outline/expand", response_model=NovelProjectSchema)
async def expand_chapters_outline(
    project_id: str,
    request: ExpandOutlineRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    prompt_service = PromptService(session)
    llm_service = LLMService(session)

    await novel_service.ensure_project_owner(project_id, current_user.id)
    outline_service = OutlineGenerationService(
        session=session,
        prompt_service=prompt_service,
        llm_service=llm_service,
        novel_service=novel_service,
    )
    try:
        await outline_service.expand_existing_outlines(
            project_id=project_id,
            user_id=current_user.id,
            start_chapter=request.start_chapter,
            end_chapter=request.end_chapter,
            batch_size=request.batch_size or DEFAULT_OUTLINE_BATCH_SIZE,
            min_summary_length=request.min_summary_length or DEFAULT_EXPAND_SUMMARY_MIN_LENGTH,
        )
    except Exception as exc:
        logger.exception("扩写章节大纲失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"扩写章节大纲失败: {str(exc)}")

    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/edit", response_model=NovelProjectSchema)
async def edit_chapter_content(
    project_id: str,
    request: EditChapterRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    
    await novel_service.ensure_project_owner(project_id, current_user.id)
    chapter = await novel_service.get_or_create_chapter(project_id, request.chapter_number)
    
    # 更新内容：优先更新选中版本，否则选最新版本或创建新版本
    target_version = chapter.selected_version
    if not target_version and chapter.versions:
        target_version = sorted(chapter.versions, key=lambda item: item.created_at)[-1]

    if target_version:
        target_version.content = request.content
        if not chapter.selected_version_id:
            chapter.selected_version_id = target_version.id
        chapter.selected_version = target_version
    else:
        target_version = ChapterVersion(
            chapter_id=chapter.id,
            content=request.content,
            version_label="manual_edit",
        )
        session.add(target_version)
        await session.flush()
        chapter.selected_version_id = target_version.id
        chapter.selected_version = target_version
    
    chapter.status = "successful"
    chapter.generation_progress = 100
    chapter.generation_step = "completed"
    chapter.generation_step_index = 7
    chapter.generation_step_total = 7
    chapter.word_count = len(request.content or "")
    await session.commit()

    background_tasks.add_task(
        _refresh_edit_summary_and_ingest,
        project_id,
        request.chapter_number,
        request.content,
        current_user.id,
    )
    background_tasks.add_task(
        _sync_foreshadowings_after_finalize,
        project_id,
        request.chapter_number,
        request.content,
    )

    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/edit-fast", response_model=ChapterSchema)
async def edit_chapter_content_fast(
    project_id: str,
    request: EditChapterRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> ChapterSchema:
    novel_service = NovelService(session)

    await novel_service.ensure_project_owner(project_id, current_user.id)
    chapter = await novel_service.get_or_create_chapter(project_id, request.chapter_number)

    target_version = chapter.selected_version
    if not target_version and chapter.versions:
        target_version = sorted(chapter.versions, key=lambda item: item.created_at)[-1]

    if target_version:
        target_version.content = request.content
        if not chapter.selected_version_id:
            chapter.selected_version_id = target_version.id
        chapter.selected_version = target_version
    else:
        target_version = ChapterVersion(
            chapter_id=chapter.id,
            content=request.content,
            version_label="manual_edit",
        )
        session.add(target_version)
        await session.flush()
        chapter.selected_version_id = target_version.id
        chapter.selected_version = target_version

    chapter.status = "successful"
    chapter.generation_progress = 100
    chapter.generation_step = "completed"
    chapter.generation_step_index = 7
    chapter.generation_step_total = 7
    chapter.word_count = len(request.content or "")
    await session.commit()

    background_tasks.add_task(
        _refresh_edit_summary_and_ingest,
        project_id,
        request.chapter_number,
        request.content,
        current_user.id,
    )
    background_tasks.add_task(
        _sync_foreshadowings_after_finalize,
        project_id,
        request.chapter_number,
        request.content,
    )

    stmt = (
        select(Chapter)
        .options(
            selectinload(Chapter.versions),
            selectinload(Chapter.evaluations),
            selectinload(Chapter.selected_version),
        )
        .where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == request.chapter_number,
        )
    )
    result = await session.execute(stmt)
    chapter = result.scalars().first()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    outline_stmt = select(ChapterOutline).where(
        ChapterOutline.project_id == project_id,
        ChapterOutline.chapter_number == request.chapter_number,
    )
    outline_result = await session.execute(outline_stmt)
    outline = outline_result.scalars().first()

    title = outline.title if outline else f"第{request.chapter_number}章"
    summary = outline.summary if outline else ""
    real_summary = chapter.real_summary
    selected_version = None
    if chapter.selected_version_id and chapter.versions:
        selected_version = next((v for v in chapter.versions if v.id == chapter.selected_version_id), None)
    if (
        selected_version is None
        and chapter.selected_version
        and (
            chapter.selected_version_id is None
            or chapter.selected_version.id == chapter.selected_version_id
        )
    ):
        selected_version = chapter.selected_version
    content = selected_version.content if selected_version else None
    versions = (
        [v.content for v in sorted(chapter.versions, key=lambda item: item.created_at)]
        if chapter.versions
        else None
    )
    evaluation_text = None
    if chapter.evaluations:
        latest = sorted(chapter.evaluations, key=lambda item: item.created_at)[-1]
        evaluation_text = latest.feedback or latest.decision
    status_value = chapter.status or ChapterGenerationStatus.NOT_GENERATED.value

    return ChapterSchema(
        chapter_number=request.chapter_number,
        title=title,
        summary=summary,
        real_summary=real_summary,
        content=content,
        versions=versions,
        evaluation=evaluation_text,
        generation_status=ChapterGenerationStatus(status_value),
        generation_progress=chapter.generation_progress,
        generation_step=chapter.generation_step,
        generation_step_index=chapter.generation_step_index,
        generation_step_total=chapter.generation_step_total,
        generation_started_at=chapter.__dict__.get("generation_started_at"),
        status_updated_at=chapter.__dict__.get("updated_at"),
        word_count=chapter.word_count or 0,
    )
