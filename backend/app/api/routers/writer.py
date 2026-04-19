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
import re
from datetime import timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...core.config import settings
from ...core.dependencies import get_current_user
from ...db.session import AsyncSessionLocal, get_session
from ...models.foreshadowing import Foreshadowing, ForeshadowingStatusHistory
from ...models.novel import Chapter, ChapterEvaluation, ChapterOutline, ChapterVersion
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
    RerollOutlineRequest,
    SelectVersionRequest,
    UpdateOutlineStatusRequest,
    UpdateChapterOutlineRequest,
)
from ...schemas.user import UserInDB

# chapter generation task registry: key = "{project_id}:{chapter_number}"
_generation_tasks: dict[str, asyncio.Task] = {}
from ...services.chapter_ingest_service import ChapterIngestionService
from ...services.llm_service import LLMService
from ...services.novel_service import NovelService
from ...services.prompt_service import PromptService
from ...services.structured_llm_service import StructuredLLMService
from ...services.vector_store_service import VectorStoreService
from ...services.finalize_service import FinalizeService
from ...services.outline_generation_service import (
    DEFAULT_EXPAND_SUMMARY_MIN_LENGTH,
    DEFAULT_OUTLINE_BATCH_SIZE,
    OutlineGenerationService,
)
from ...utils.json_utils import remove_think_tags
from ...services.pipeline_orchestrator import PipelineOrchestrator

router = APIRouter(prefix="/api/writer", tags=["Writer"])
logger = logging.getLogger(__name__)
# 使用固定 UTC+8，避免在 Windows/Python 环境缺少 tzdata 时 ZoneInfo 初始化失败。
CN_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_CHAPTER_TARGET_WORD_COUNT = 3000
MIN_CHAPTER_WORD_COUNT = 2200
WRITER_GENERATION_MAX_TOKENS = 7000


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



async def _load_project_schema(service: NovelService, project_id: str, user_id: int) -> NovelProjectSchema:
    return await service.get_project_schema(project_id, user_id)



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
    """后台任务：在章节定稿/手改后同步伏笔表。SQLite 并发写入可能短暂锁库，最多重试 3 次。"""
    for attempt in range(3):
        if attempt:
            await asyncio.sleep(2 ** attempt)  # 2s, 4s
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
                return
            except SAOperationalError as exc:
                await session.rollback()
                if "database is locked" in str(exc) and attempt < 2:
                    logger.warning("伏笔同步遇到数据库锁，第 %d 次重试 project=%s chapter=%s", attempt + 1, project_id, chapter_number)
                    continue
                logger.exception("伏笔同步失败 project=%s chapter=%s err=%s", project_id, chapter_number, exc)
                return
            except Exception as exc:
                await session.rollback()
                logger.exception("伏笔同步失败 project=%s chapter=%s err=%s", project_id, chapter_number, exc)
                return



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
    key = f"{request.project_id}:{request.chapter_number}"
    existing = _generation_tasks.get(key)
    if existing and not existing.done():
        raise HTTPException(status_code=409, detail="该章节正在生成中，请勿重复提交")
    orchestrator = PipelineOrchestrator(session)
    task = asyncio.create_task(
        orchestrator.generate_chapter(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            writing_notes=request.writing_notes,
            user_id=current_user.id,
            flow_config=request.flow_config.model_dump(),
        )
    )
    _generation_tasks[key] = task
    try:
        result = await task
    except asyncio.CancelledError:
        try:
            await session.rollback()
        except Exception:
            pass
        raise
    except Exception:
        try:
            await session.rollback()
            chapter = await NovelService(session).get_or_create_chapter(
                request.project_id, request.chapter_number
            )
            if chapter.status == "generating":
                chapter.status = "failed"
                chapter.generation_progress = 0
                chapter.generation_step = "failed"
                await session.commit()
        except Exception:
            pass
        raise
    finally:
        if _generation_tasks.get(key) is task:
            _generation_tasks.pop(key)

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
    key = f"{project_id}:{request.chapter_number}"
    existing = _generation_tasks.get(key)
    if existing and not existing.done():
        raise HTTPException(status_code=409, detail="该章节正在生成中，请勿重复提交")
    orchestrator = PipelineOrchestrator(session)
    task = asyncio.create_task(
        orchestrator.generate_chapter(
            project_id=project_id,
            chapter_number=request.chapter_number,
            writing_notes=request.writing_notes,
            user_id=current_user.id,
            flow_config={
                "preset": "basic",
                "target_word_count": request.target_word_count,
            },
        )
    )
    _generation_tasks[key] = task
    try:
        await task
    except asyncio.CancelledError:
        try:
            await session.rollback()
        except Exception:
            pass
    except Exception:
        try:
            await session.rollback()
            chapter = await NovelService(session).get_or_create_chapter(project_id, request.chapter_number)
            if chapter.status == "generating":
                chapter.status = "failed"
                chapter.generation_progress = 0
                chapter.generation_step = "failed"
                await session.commit()
        except Exception:
            pass
        raise
    finally:
        if _generation_tasks.get(key) is task:
            _generation_tasks.pop(key)
    novel_service = NovelService(session)
    return await _load_project_schema(novel_service, project_id, current_user.id)


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
    task = _generation_tasks.get(key)
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

    version_to_evaluate_id = version_to_evaluate.id

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
        project_schema = await novel_service.get_project_schema(project_id, current_user.id)
        completed_chapters_payload = []
        for item in sorted(project_schema.chapters, key=lambda ch: ch.chapter_number):
            if item.chapter_number >= request.chapter_number:
                continue
            summary = item.real_summary or item.summary or ""
            if not summary and not item.content:
                continue
            completed_chapters_payload.append(
                {
                    "chapter_number": item.chapter_number,
                    "title": item.title,
                    "summary": summary,
                }
            )
        evaluation_input = {
            "novel_blueprint": project_schema.blueprint.model_dump() if project_schema.blueprint else {},
            "completed_chapters": completed_chapters_payload,
            "content_to_evaluate": {
                "chapter_title": version_to_evaluate.version_label or f"第{request.chapter_number}章",
                "versions": [
                    {
                        "version_number": 1,
                        "content": version_to_evaluate.content,
                    }
                ],
            },
        }
        evaluation_payload = await StructuredLLMService(llm_service).generate_json(
            system_prompt=eval_prompt,
            user_content=json.dumps(evaluation_input, ensure_ascii=False),
            temperature=0.3,
            user_id=current_user.id,
            role="reviewer",
            content_rating=content_rating,
        )
        evaluation_text = json.dumps(evaluation_payload, ensure_ascii=False)
        
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
            evaluation_record = ChapterEvaluation(
                chapter_id=chapter.id,
                version_id=version_to_evaluate_id,
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

        # 评审失败时返回项目快照，让前端直接感知 evaluation_failed 状态，
        # 避免评审阶段因为第三方波动反复弹 500。
        return await _load_project_schema(novel_service, project_id, current_user.id)
    
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

    await novel_service.delete_chapters(project_id, request.chapter_numbers)
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


@router.post("/novels/{project_id}/chapters/outline/status", response_model=NovelProjectSchema)
async def update_outline_status(
    project_id: str,
    request: UpdateOutlineStatusRequest,
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
    await outline_service.update_outline_status(
        project_id=project_id,
        chapter_numbers=request.chapter_numbers,
        status=request.status,
    )
    return await _load_project_schema(novel_service, project_id, current_user.id)


@router.post("/novels/{project_id}/chapters/outline/reroll", response_model=NovelProjectSchema)
async def reroll_chapters_outline(
    project_id: str,
    request: RerollOutlineRequest,
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
        await outline_service.reroll_outlines(
            project_id=project_id,
            user_id=current_user.id,
            chapter_numbers=request.chapter_numbers,
            batch_size=request.batch_size or DEFAULT_OUTLINE_BATCH_SIZE,
            only_needs_regen=request.only_needs_regen,
        )
    except Exception as exc:
        logger.exception("重生成章节大纲失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"重生成章节大纲失败: {str(exc)}")
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
