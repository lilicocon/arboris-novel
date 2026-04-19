# AIMETA P=写作流水线编排_统一生成入口|R=上下文汇聚_生成_审查_优化|NR=不含API路由|E=PipelineOrchestrator|X=internal|A=编排器|D=fastapi,sqlalchemy|S=db,net|RD=./README.ai
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.session import AsyncSessionLocal
from ..models.novel import Chapter
from ..models.project_memory import ProjectMemory
from ..repositories.system_config_repository import SystemConfigRepository
from ..services.ai_review_service import AIReviewService
from ..services.chapter_context_service import ChapterContextService
from ..services.chapter_guardrails import ChapterGuardrails
from ..services.consistency_service import ConsistencyService, ViolationSeverity
from ..services.enhanced_writing_flow import EnhancedWritingFlow
from ..services.enrichment_service import EnrichmentService
from ..services.llm_service import LLMService
from ..services.knowledge_retrieval_service import KnowledgeRetrievalService, FilteredContext
from ..services.memory_layer_service import MemoryLayerService
from ..services.novel_service import NovelService
from ..services.preview_generation_service import PreviewGenerationService
from ..services.prompt_service import PromptService
from ..services.reader_simulator_service import ReaderSimulatorService, ReaderType
from ..services.self_critique_service import CritiqueDimension, SelfCritiqueService
from ..services.structured_llm_service import StructuredLLMService
from ..services.vector_store_service import VectorStoreService
from ..services.writer_context_builder import WriterContextBuilder
from ..services.context_budgeter import ContextBudgeter
from ..utils.json_utils import remove_think_tags, unwrap_markdown_json

logger = logging.getLogger(__name__)
# 使用固定 UTC+8，避免在 Windows/Python 环境缺少 tzdata 时 ZoneInfo 初始化失败。
CN_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
MIN_CHAPTER_VERSION_COUNT = 1
MAX_CHAPTER_VERSION_COUNT = 2
RETRY_REDUCED_CONTEXT_BUDGET = 12000


def _clamp_version_count(value: int) -> int:
    return max(MIN_CHAPTER_VERSION_COUNT, min(MAX_CHAPTER_VERSION_COUNT, int(value)))
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


_DEFAULT_STYLE_HINTS: list[str] = [
    "情绪更细腻，节奏更慢，多写内心戏和感官描写",
    "冲突更强，节奏更快，多写动作和对话",
    "悬念更重，多埋伏笔，结尾钩子更强",
]


@dataclass
class PipelineConfig:
    preset: str = "basic"
    version_count: int = 1
    enable_preview: bool = False
    enable_optimizer: bool = False
    enable_consistency: bool = False
    enable_enrichment: Optional[bool] = None
    async_finalize: bool = False
    enable_constitution: bool = False
    enable_persona: bool = False
    enable_six_dimension: bool = False
    enable_reader_sim: bool = False
    enable_self_critique: bool = False
    enable_memory: bool = False
    enable_rag: bool = True
    rag_mode: str = "simple"
    enable_foreshadowing: bool = False
    enable_faction: bool = False


class PipelineOrchestrator:
    """统一写作流水线编排器。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.llm_service = LLMService(session)
        self.prompt_service = PromptService(session)
        self.novel_service = NovelService(session)
        self.context_builder = WriterContextBuilder()
        self.guardrails = ChapterGuardrails()

    async def generate_chapter(
        self,
        *,
        project_id: str,
        chapter_number: int,
        user_id: int,
        writing_notes: Optional[str] = None,
        flow_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        config = await self._resolve_config(flow_config)
        project = await self.novel_service.ensure_project_owner(project_id, user_id)

        request_target = None
        if flow_config and flow_config.get("target_word_count"):
            request_target = flow_config["target_word_count"]

        outline = await self.novel_service.get_outline(project_id, chapter_number)
        if not outline:
            raise HTTPException(status_code=404, detail="蓝图中未找到对应章节纲要")

        chapter = await self.novel_service.get_or_create_chapter(project_id, chapter_number)
        chapter.real_summary = None
        chapter.selected_version_id = None
        chapter.selected_version = None
        chapter.status = "generating"
        chapter.generation_started_at = datetime.now(CN_TIMEZONE)
        chapter.generation_progress = 3
        chapter.generation_step = "context_prep"
        chapter.generation_step_index = 1
        chapter.generation_step_total = 7
        await self.session.commit()
        logger.info("[%s] ch%s ▶ [1/7] 3%% 准备上下文...", project_id[:8], chapter_number)

        outlines_map = {item.chapter_number: item for item in project.outlines}
        history_context = await self._collect_history_context(
            project_id=project_id,
            chapter_number=chapter_number,
            outlines_map=outlines_map,
            chapters=project.chapters,
            user_id=user_id,
        )

        project_schema = await self.novel_service._serialize_project(project)
        if not project_schema.blueprint:
            raise HTTPException(status_code=422, detail="Project has no blueprint yet")
        blueprint_dict = self._normalize_blueprint(project_schema.blueprint.model_dump())
        content_rating = str(blueprint_dict.get("content_rating") or "safe").lower()

        target_word_count = _resolve_target_word_count(
            request_target=request_target,
            blueprint_chapter_length=blueprint_dict.get("chapter_length"),
        )
        min_word_count = int(target_word_count * 0.73)
        max_tokens = _calc_max_tokens(target_word_count)

        outline_title = outline.title or f"第{outline.chapter_number}章"
        outline_summary = outline.summary or "暂无摘要"
        writing_notes = writing_notes or "无额外写作指令"

        all_characters = [c.get("name") for c in blueprint_dict.get("characters", []) if c.get("name")]

        project_memory_text = await self._get_project_memory_text(project_id)

        chapter_mission = await self._generate_chapter_mission(
            blueprint_dict=blueprint_dict,
            previous_summary=history_context["previous_summary"],
            previous_tail=history_context["previous_tail"],
            outline_title=outline_title,
            outline_summary=outline_summary,
            writing_notes=writing_notes,
            introduced_characters=[],
            all_characters=all_characters,
            user_id=user_id,
            content_rating=content_rating,
        )

        rag_context = None
        knowledge_context = None
        rag_stats = None
        if config.enable_rag:
            if config.rag_mode == "two_stage":
                knowledge_context, rag_stats = await self._get_two_stage_rag_context(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    writing_notes=writing_notes,
                    pov_character=self._resolve_pov_character(chapter_mission),
                    user_id=user_id,
                )
            else:
                rag_context = await self._get_rag_context(
                    project_id=project_id,
                    outline_title=outline_title,
                    outline_summary=outline_summary,
                    writing_notes=writing_notes,
                    user_id=user_id,
                )
                rag_stats = {
                    "mode": "simple",
                    "chunks": len(rag_context.get("chunks", [])) if rag_context else 0,
                    "summaries": len(rag_context.get("summaries", [])) if rag_context else 0,
                }

        chapter.generation_progress = 28
        chapter.generation_step = "director_mission"
        chapter.generation_step_index = 2
        await self.session.commit()
        logger.info("[%s] ch%s ▶ [2/7] 28%% 生成章节导演脚本...", project_id[:8], chapter_number)

        allowed_new_characters = chapter_mission.get("allowed_new_characters", []) if chapter_mission else []

        visibility_context = self.context_builder.build_visibility_context(
            blueprint=blueprint_dict,
            completed_summaries=history_context["completed_summaries"],
            previous_tail=history_context["previous_tail"],
            outline_title=outline_title,
            outline_summary=outline_summary,
            writing_notes=writing_notes,
            allowed_new_characters=allowed_new_characters,
        )

        writer_blueprint = visibility_context["writer_blueprint"]
        forbidden_characters = visibility_context["forbidden_characters"]
        introduced_characters = visibility_context["introduced_characters"]

        logger.info(
            "Pipeline context: project=%s chapter=%s introduced=%d allowed_new=%d forbidden=%d",
            project_id,
            chapter_number,
            len(introduced_characters),
            len(allowed_new_characters),
            len(forbidden_characters),
        )

        enhanced_flow = None
        enhanced_context = None
        if config.enable_constitution or config.enable_persona or config.enable_foreshadowing or config.enable_faction:
            enhanced_flow = EnhancedWritingFlow(self.session, self.llm_service, self.prompt_service)
            enhanced_context = await enhanced_flow.prepare_writing_context(
                project_id=project_id,
                chapter_number=chapter_number,
                chapter_outline=outline_summary,
            )

        memory_context = None
        if config.enable_memory:
            memory_context = await self._get_memory_context(
                project_id=project_id,
                chapter_number=chapter_number,
                involved_characters=introduced_characters,
            )

        writer_prompt = await self.prompt_service.get_prompt("writing_v2")
        if not writer_prompt:
            writer_prompt = await self.prompt_service.get_prompt("writing")
        if not writer_prompt:
            raise HTTPException(status_code=500, detail="缺少写作提示词，请联系管理员配置")

        prompt_sections = self._build_prompt_sections(
            writer_blueprint=writer_blueprint,
            previous_summary=history_context["previous_summary"],
            previous_tail=history_context["previous_tail"],
            chapter_mission=chapter_mission,
            rag_context=rag_context,
            knowledge_context=knowledge_context,
            outline_title=outline_title,
            outline_summary=outline_summary,
            writing_notes=writing_notes,
            forbidden_characters=forbidden_characters,
            project_memory_text=project_memory_text,
            memory_context=memory_context,
            target_word_count=target_word_count,
            min_word_count=min_word_count,
        )

        if enhanced_flow and enhanced_context:
            prompt_sections = enhanced_flow.build_enhanced_prompt_sections(prompt_sections, enhanced_context)

        prompt_sections = ContextBudgeter(total_budget_tokens=16000).fit(prompt_sections)
        prompt_input = self._compose_prompt_input(prompt_sections)
        logger.debug("Pipeline prompt length: %s chars", len(prompt_input))
        chapter.generation_progress = 55
        chapter.generation_step = "draft_generation"
        chapter.generation_step_index = 4
        await self.session.commit()
        logger.info("[%s] ch%s ▶ [4/7] 55%% 正在生成草稿 (x%d 版本)...", project_id[:8], chapter_number, config.version_count)

        version_count = config.version_count
        version_style_hints = self._resolve_style_hints(enhanced_context, version_count)

        version_factories: List[Callable[[], Awaitable[Dict[str, Any]]]] = []
        for idx in range(version_count):
            style_hint = version_style_hints[idx] if idx < len(version_style_hints) else None

            def build_factory(
                *,
                index: int = idx,
                resolved_style_hint: Optional[str] = style_hint,
            ) -> Callable[[], Awaitable[Dict[str, Any]]]:
                return lambda: self._generate_single_version_with_isolated_session(
                    index=index,
                    prompt_input=prompt_input,
                    writer_prompt=writer_prompt,
                    style_hint=resolved_style_hint,
                    project_id=project_id,
                    chapter_number=chapter_number,
                    outline_title=outline_title,
                    outline_summary=outline_summary,
                    chapter_mission=chapter_mission,
                    forbidden_characters=forbidden_characters,
                    allowed_new_characters=allowed_new_characters,
                    user_id=user_id,
                    writer_blueprint=writer_blueprint,
                    memory_context=memory_context,
                    enhanced_context=enhanced_context,
                    config=config,
                    max_tokens=max_tokens,
                    target_word_count=target_word_count,
                    prompt_sections=prompt_sections,
                    content_rating=content_rating,
                )
            version_factories.append(build_factory())

        versions = await self._generate_versions_in_parallel(
            chapter=chapter,
            version_factories=version_factories,
        )

        chapter.generation_progress = 86
        chapter.generation_step = "quality_review"
        chapter.generation_step_index = 5
        await self.session.commit()
        logger.info("[%s] ch%s ▶ [5/7] 86%% 质量审查 (AI评审/自评/一致性)...", project_id[:8], chapter_number)
        best_version_index, ai_review_result = await self._run_ai_review(
            versions=versions,
            chapter_mission=chapter_mission,
            user_id=user_id,
            content_rating=content_rating,
        )

        review_summaries: Dict[str, Any] = {}
        if ai_review_result:
            review_summaries["ai_review"] = ai_review_result

        ai_best_version_index = best_version_index
        if versions:
            best_version_index = self._select_preferred_version_index(
                versions=versions,
                ai_best_version_index=best_version_index,
                target_word_count=target_word_count,
            )
        else:
            best_version_index = 0

        if versions:
            best_version_index = max(0, min(best_version_index, len(versions) - 1))
            best_version = versions[best_version_index]
            best_content = best_version["content"]

            if enhanced_flow and config.enable_six_dimension:
                logger.info("[%s] ch%s   → 六维评审...", project_id[:8], chapter_number)
                review_result = await enhanced_flow.post_generation_review(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    chapter_title=outline_title,
                    chapter_content=best_content,
                    chapter_plan=json.dumps(chapter_mission, ensure_ascii=False) if chapter_mission else None,
                    previous_summary=history_context["previous_summary"],
                )
                review_summaries["enhanced_review"] = review_result

            if config.enable_self_critique:
                logger.info("[%s] ch%s   → 自我评审...", project_id[:8], chapter_number)
                best_content, critique_summary = await self._run_self_critique(
                    best_content,
                    user_id=user_id,
                    context={
                        "character_profiles": json.dumps(writer_blueprint.get("characters", []), ensure_ascii=False),
                        "previous_summary": history_context["previous_summary"],
                    },
                )
                review_summaries["self_critique"] = critique_summary

            if config.enable_reader_sim:
                logger.info("[%s] ch%s   → 读者模拟...", project_id[:8], chapter_number)
                reader_feedback = await self._run_reader_simulation(
                    best_content,
                    chapter_number=chapter_number,
                    previous_summary=history_context["previous_summary"],
                    user_id=user_id,
                )
                review_summaries["reader_simulator"] = reader_feedback

            if config.enable_consistency:
                logger.info("[%s] ch%s   → 一致性检查...", project_id[:8], chapter_number)
                best_content, consistency_report = await self._run_consistency_check(
                    project_id=project_id,
                    chapter_text=best_content,
                    user_id=user_id,
                )
                review_summaries["consistency"] = consistency_report

            if config.enable_optimizer:
                logger.info("[%s] ch%s   → 文本优化...", project_id[:8], chapter_number)
                best_content, optimizer_report = await self._run_optimizer(
                    best_content,
                    user_id=user_id,
                    content_rating=content_rating,
                )
                review_summaries["optimizer"] = optimizer_report

            if self._should_run_enrichment(
                chapter_content=best_content,
                target_word_count=target_word_count,
                configured_enrichment=config.enable_enrichment,
            ):
                logger.info("[%s] ch%s   → 内容扩写...", project_id[:8], chapter_number)
                best_content, enrichment_report = await self._run_enrichment(
                    best_content,
                    user_id=user_id,
                    target_word_count=target_word_count,
                    content_rating=content_rating,
                )
                if enrichment_report:
                    review_summaries["enrichment"] = enrichment_report

            best_version["content"] = best_content
            best_version.setdefault("metadata", {})["review_summaries"] = review_summaries

        contents = [v.get("content", "") for v in versions]
        metadata: List[Dict[str, Any]] = [v.get("metadata") or {} for v in versions]
        chapter.generation_progress = 96
        chapter.generation_step = "persist_versions"
        chapter.generation_step_index = 6
        await self.session.commit()
        logger.info("[%s] ch%s ▶ [6/7] 96%% 持久化版本...", project_id[:8], chapter_number)
        versions_models = await self.novel_service.replace_chapter_versions(chapter, contents, metadata)

        variants = []
        for idx, version_model in enumerate(versions_models):
            variant = {
                "index": idx,
                "version_id": version_model.id,
                "content": versions[idx].get("content", ""),
                "metadata": versions[idx].get("metadata"),
            }
            variants.append(variant)

        logger.info(
            "[%s] ch%s ✓ [7/7] 100%% 完成 (最佳版本=%d, 共%d字)",
            project_id[:8], chapter_number, best_version_index,
            self._count_text_length(versions[best_version_index].get("content", "")) if versions else 0,
        )

        return {
            "project_id": project_id,
            "chapter_number": chapter_number,
            "preset": config.preset,
            "best_version_index": best_version_index,
            "variants": variants,
            "review_summaries": review_summaries,
            "debug_metadata": {
                "version_count": version_count,
                "stages": self._build_stage_flags(config),
                "retrieval_stats": rag_stats,
                "ai_best_version_index": ai_best_version_index,
                "selected_version_lengths": [
                    self._count_text_length(item.get("content", ""))
                    for item in versions
                ],
            },
        }

    async def _resolve_config(self, flow_config: Optional[Dict[str, Any]]) -> PipelineConfig:
        flow_config = flow_config or {}
        preset = flow_config.get("preset", "basic")

        config = PipelineConfig(preset=preset)
        config.version_count = await self._resolve_version_count(flow_config.get("versions"))

        if preset in ("enhanced", "ultimate"):
            config.enable_constitution = True
            config.enable_persona = True
            config.enable_foreshadowing = True
            config.enable_faction = True
            config.rag_mode = "two_stage"

        if preset == "enhanced":
            config.enable_six_dimension = True

        if preset == "ultimate":
            config.enable_memory = True

        if preset == "basic":
            config.enable_rag = True

        for key in (
            "enable_preview",
            "enable_optimizer",
            "enable_consistency",
            "enable_enrichment",
            "async_finalize",
            "enable_rag",
        ):
            if key in flow_config and flow_config[key] is not None:
                setattr(config, key, bool(flow_config[key]))

        if flow_config.get("rag_mode"):
            config.rag_mode = str(flow_config["rag_mode"])

        if preset == "ultimate":
            config.enable_preview = False
            config.enable_optimizer = False
            config.enable_consistency = False
            config.enable_enrichment = False
            config.enable_six_dimension = False
            config.enable_reader_sim = False
            config.enable_self_critique = False
            for key in ("enable_self_critique", "enable_reader_sim", "enable_six_dimension", "enable_enrichment"):
                if flow_config.get(key) is True:
                    logger.warning("ultimate preset: overriding %s=True → False", key)
                    flow_config[key] = False

        return config

    @staticmethod
    def _count_text_length(text: str) -> int:
        return len(re.sub(r"\s+", "", text or ""))

    @staticmethod
    def _compose_prompt_input(
        prompt_sections: List[Tuple[str, str]],
        style_hint: Optional[str] = None,
    ) -> str:
        prompt_input = "\n\n".join(f"{title}\n{content}" for title, content in prompt_sections if content)
        if style_hint:
            prompt_input += f"\n\n[版本风格提示]\n{style_hint}"
        return prompt_input

    @classmethod
    def _select_preferred_version_index(
        cls,
        *,
        versions: List[Dict[str, Any]],
        ai_best_version_index: int,
        target_word_count: int,
    ) -> int:
        if not versions:
            return 0

        normalized_ai_index = max(0, min(ai_best_version_index, len(versions) - 1))
        if target_word_count <= 0:
            return normalized_ai_index

        lengths = [cls._count_text_length(item.get("content", "")) for item in versions]
        if lengths[normalized_ai_index] >= target_word_count:
            return normalized_ai_index

        reached_target = [
            (index, length)
            for index, length in enumerate(lengths)
            if length >= target_word_count
        ]
        if reached_target:
            return min(
                reached_target,
                key=lambda item: (item[1] - target_word_count, item[0]),
            )[0]

        return max(
            range(len(lengths)),
            key=lambda index: (lengths[index], index == normalized_ai_index, -index),
        )

    @classmethod
    def _should_run_enrichment(
        cls,
        *,
        chapter_content: str,
        target_word_count: int,
        configured_enrichment: Optional[bool],
        auto_threshold: float = 0.8,
    ) -> bool:
        if configured_enrichment is True:
            return True
        if configured_enrichment is False:
            return False
        if target_word_count <= 0:
            return False
        return cls._count_text_length(chapter_content) < target_word_count * auto_threshold

    @staticmethod
    def _sanitize_enrichment_output(text: str) -> str:
        if not text:
            return text

        cleaned_lines: List[str] = []
        for raw_line in text.strip().splitlines():
            line = raw_line.strip()
            if not line:
                cleaned_lines.append("")
                continue
            if re.fullmatch(r"-{3,}", line):
                continue
            if "以下是" in line and "完整章节内容" in line:
                continue
            if re.match(r"^[（(]\s*注[:：]", line):
                continue
            if re.fullmatch(r"[（(]本章完[）)]", line):
                continue
            if "实际字数约" in line:
                continue
            cleaned_lines.append(raw_line.rstrip())

        cleaned = "\n".join(cleaned_lines).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    async def _resolve_version_count(self, requested_count: Optional[int]) -> int:
        if requested_count:
            try:
                count = int(requested_count)
                return _clamp_version_count(count)
            except (TypeError, ValueError):
                pass

        repo = SystemConfigRepository(self.session)
        records = await repo.get_by_keys(("writer.chapter_versions", "writer.version_count"))
        for key in ("writer.chapter_versions", "writer.version_count"):
            record = records.get(key)
            if record and record.value:
                try:
                    val = int(record.value)
                    if val >= 1:
                        return _clamp_version_count(val)
                except ValueError:
                    pass

        for env in ("WRITER_CHAPTER_VERSION_COUNT", "WRITER_CHAPTER_VERSIONS", "WRITER_VERSION_COUNT"):
            v = os.getenv(env)
            if v:
                try:
                    val = int(v)
                    if val >= 1:
                        return _clamp_version_count(val)
                except ValueError:
                    pass

        return _clamp_version_count(int(settings.writer_chapter_versions))

    async def _collect_history_context(
        self,
        *,
        project_id: str,
        chapter_number: int,
        outlines_map: Dict[int, Any],
        chapters: List[Chapter],
        user_id: int,
    ) -> Dict[str, Any]:
        prev_chapters = [
            c for c in chapters
            if c.chapter_number < chapter_number
            and c.selected_version is not None
            and c.selected_version.content
        ]
        need_summary = [c for c in prev_chapters if not c.real_summary]

        if need_summary:
            sem = asyncio.Semaphore(3)

            async def _summarize(content: str) -> str:
                async with sem:
                    async with AsyncSessionLocal() as iso_session:
                        svc = LLMService(iso_session)
                        raw = await svc.get_summary(content, temperature=0.15, user_id=user_id, timeout=180.0)
                        return remove_think_tags(raw)

            results = await asyncio.gather(
                *(_summarize(c.selected_version.content) for c in need_summary),
                return_exceptions=True,
            )
            for chapter_obj, result in zip(need_summary, results):
                if isinstance(result, Exception):
                    logger.warning("章节 %d 摘要生成失败，跳过: %s", chapter_obj.chapter_number, result)
                else:
                    chapter_obj.real_summary = result
            await self.session.commit()

        completed_chapters = []
        completed_summaries = []
        latest_prev_number = -1
        previous_summary_text = ""
        previous_tail_excerpt = ""

        for existing in prev_chapters:
            _ol = outlines_map.get(existing.chapter_number)
            completed_chapters.append(
                {
                    "chapter_number": existing.chapter_number,
                    "title": _ol.title if _ol else f"第{existing.chapter_number}章",
                    "summary": existing.real_summary,
                }
            )
            completed_summaries.append(existing.real_summary or "")

            if existing.chapter_number > latest_prev_number:
                latest_prev_number = existing.chapter_number
                previous_summary_text = existing.real_summary or ""
                previous_tail_excerpt = self._extract_tail_excerpt(existing.selected_version.content)

        return {
            "completed_chapters": completed_chapters,
            "completed_summaries": completed_summaries,
            "previous_summary": previous_summary_text or "暂无（这是第一章）",
            "previous_tail": previous_tail_excerpt or "暂无（这是第一章）",
        }

    @staticmethod
    def _extract_tail_excerpt(text: Optional[str], limit: int = 500) -> str:
        if not text:
            return ""
        stripped = text.strip()
        if len(stripped) <= limit:
            return stripped
        return stripped[-limit:]

    @staticmethod
    def _normalize_blueprint(blueprint_dict: Dict[str, Any]) -> Dict[str, Any]:
        if "relationships" in blueprint_dict and blueprint_dict["relationships"]:
            for relation in blueprint_dict["relationships"]:
                if "character_from" in relation:
                    relation["from"] = relation.pop("character_from")
                if "character_to" in relation:
                    relation["to"] = relation.pop("character_to")
        return blueprint_dict

    async def _generate_chapter_mission(
        self,
        *,
        blueprint_dict: Dict[str, Any],
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
        plan_prompt = await self.prompt_service.get_prompt("chapter_plan")
        if not plan_prompt:
            logger.warning("未配置 chapter_plan 提示词，跳过导演脚本生成")
            return None

        plan_input = f"""
[上一章摘要]
{previous_summary}

[上一章结尾]
{previous_tail}

[当前章节大纲]
标题：{outline_title}
摘要：{outline_summary}

[已登场角色]
{json.dumps(introduced_characters, ensure_ascii=False) if introduced_characters else "暂无"}

[全部角色]
{json.dumps(all_characters, ensure_ascii=False)}

[写作指令]
{writing_notes}
"""

        try:
            mission = await StructuredLLMService(self.llm_service).generate_json(
                system_prompt=plan_prompt,
                user_content=plan_input,
                temperature=0.3,
                user_id=user_id,
                role="writer",
                content_rating=content_rating,
                timeout=120.0,
            )
            logger.info("章节导演脚本生成完成: macro_beat=%s", mission.get("macro_beat"))
            return mission
        except Exception as exc:
            logger.warning("生成章节导演脚本失败，将使用默认模式: %s", exc)
            return None

    async def _get_rag_context(
        self,
        *,
        project_id: str,
        outline_title: str,
        outline_summary: str,
        writing_notes: str,
        user_id: int,
    ) -> Dict[str, Any]:
        if not settings.vector_store_enabled:
            return {"chunks": [], "summaries": []}

        try:
            vector_store = VectorStoreService()
        except RuntimeError as exc:
            logger.warning("向量库初始化失败，跳过 RAG: %s", exc)
            return {"chunks": [], "summaries": []}

        query_parts = [outline_title, outline_summary]
        if writing_notes:
            query_parts.append(writing_notes)
        rag_query = "\n".join(part for part in query_parts if part)

        context_service = ChapterContextService(llm_service=self.llm_service, vector_store=vector_store)
        rag_context = await context_service.retrieve_for_generation(
            project_id=project_id,
            query_text=rag_query or outline_title or outline_summary,
            user_id=user_id,
        )
        return {
            "chunks": rag_context.chunk_texts() if rag_context.chunks else [],
            "summaries": rag_context.summary_lines() if rag_context.summaries else [],
        }

    async def _get_two_stage_rag_context(
        self,
        *,
        project_id: str,
        chapter_number: int,
        writing_notes: str,
        pov_character: Optional[str],
        user_id: int,
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        if not settings.vector_store_enabled:
            return None, {"mode": "two_stage", "enabled": False}

        try:
            vector_store = VectorStoreService()
        except RuntimeError as exc:
            logger.warning("向量库初始化失败，跳过两层 RAG: %s", exc)
            return None, {"mode": "two_stage", "enabled": False, "error": str(exc)}

        sync_session = getattr(self.session, "sync_session", self.session)
        retrieval_service = KnowledgeRetrievalService(sync_session, self.llm_service, vector_store)
        filtered = await retrieval_service.retrieve_and_filter(
            project_id=project_id,
            chapter_number=chapter_number,
            user_id=user_id,
            pov_character=pov_character,
            user_guidance=writing_notes,
            top_k=settings.vector_top_k_chunks,
        )
        context_text = self._format_filtered_context(filtered)
        stats = filtered.stats or {}
        stats["mode"] = "two_stage"
        return context_text, stats

    async def _get_project_memory_text(self, project_id: str) -> Optional[str]:
        result = await self.session.execute(
            select(ProjectMemory).where(ProjectMemory.project_id == project_id)
        )
        memory = result.scalars().first()
        if not memory:
            return None

        parts = []
        if memory.global_summary:
            parts.append(f"### 全局摘要\n{memory.global_summary}")
        if memory.plot_arcs:
            parts.append("### 剧情线追踪\n" + json.dumps(memory.plot_arcs, ensure_ascii=False, indent=2))
        if not parts:
            return None
        return "\n\n".join(parts)

    async def _get_memory_context(
        self,
        *,
        project_id: str,
        chapter_number: int,
        involved_characters: List[str],
    ) -> str:
        memory_layer = MemoryLayerService(self.session, self.llm_service, self.prompt_service)
        return await memory_layer.get_memory_context(project_id, chapter_number, involved_characters)

    @staticmethod
    def _build_prompt_sections(
        *,
        writer_blueprint: Dict[str, Any],
        previous_summary: str,
        previous_tail: str,
        chapter_mission: Optional[dict],
        rag_context: Optional[Dict[str, Any]],
        knowledge_context: Optional[str],
        outline_title: str,
        outline_summary: str,
        writing_notes: str,
        forbidden_characters: List[str],
        project_memory_text: Optional[str],
        memory_context: Optional[str],
        target_word_count: int = DEFAULT_CHAPTER_TARGET_WORD_COUNT,
        min_word_count: int = MIN_CHAPTER_WORD_COUNT,
    ) -> List[Tuple[str, str]]:
        blueprint_text = json.dumps(writer_blueprint, ensure_ascii=False, indent=2)
        mission_text = json.dumps(chapter_mission, ensure_ascii=False, indent=2) if chapter_mission else "无导演脚本"
        forbidden_text = json.dumps(forbidden_characters, ensure_ascii=False) if forbidden_characters else "无"

        sections: List[Tuple[str, str]] = [
            ("[世界蓝图](JSON，已裁剪)", blueprint_text),
        ]

        if project_memory_text:
            sections.append(("[项目长期记忆](摘要/剧情线)", project_memory_text))
        if memory_context:
            sections.append(("[记忆层上下文]", memory_context))

        sections.extend(
            [
                ("[上一章摘要]", previous_summary or "暂无（这是第一章）"),
                ("[上一章结尾]", previous_tail or "暂无（这是第一章）"),
                ("[章节导演脚本](JSON)", mission_text),
            ]
        )

        if knowledge_context:
            sections.append(("[RAG精筛上下文](含POV裁剪)", knowledge_context))

        if rag_context:
            rag_chunks_text = "\n\n".join(rag_context.get("chunks", [])) or "未检索到章节片段"
            rag_summaries_text = "\n".join(rag_context.get("summaries", [])) or "未检索到章节摘要"
            sections.append(("[检索到的剧情上下文](Markdown)", rag_chunks_text))
            sections.append(("[检索到的章节摘要](Markdown)", rag_summaries_text))

        sections.extend(
            [
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
        )

        return sections

    @staticmethod
    def _resolve_style_hints(
        enhanced_context: Optional[Dict[str, Any]],
        version_count: int,
    ) -> List[str]:
        if enhanced_context and enhanced_context.get("version_style_hints"):
            hints = enhanced_context["version_style_hints"]
            if isinstance(hints, list) and hints:
                return hints[:version_count]
        return _DEFAULT_STYLE_HINTS[:version_count]

    @staticmethod
    def _resolve_pov_character(chapter_mission: Optional[dict]) -> Optional[str]:
        if not chapter_mission:
            return None
        return chapter_mission.get("pov") or chapter_mission.get("pov_character")

    async def _generate_versions_in_parallel(
        self,
        *,
        chapter: Chapter,
        version_factories: List[Callable[[], Awaitable[Dict[str, Any]]]],
    ) -> List[Dict[str, Any]]:
        if not version_factories:
            return []

        if self._should_serialize_version_generation():
            serial_versions: List[Dict[str, Any]] = []
            for index, factory in enumerate(version_factories):
                version = await factory()
                serial_versions.append(version)
                chapter.generation_progress = 55 + int(((index + 1) / len(version_factories)) * 25)
                chapter.generation_step = "draft_generation"
                chapter.generation_step_index = 4
                await self.session.commit()
            return serial_versions

        tasks = [asyncio.ensure_future(factory()) for factory in version_factories]
        parallel_results: List[Optional[Dict[str, Any]]] = [None] * len(tasks)
        completed = 0

        try:
            for task in asyncio.as_completed(tasks):
                version = await task
                parallel_results[version["index"]] = version
                completed += 1
                chapter.generation_progress = 55 + int((completed / len(tasks)) * 25)
                chapter.generation_step = "draft_generation"
                chapter.generation_step_index = 4
                await self.session.commit()
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        return [item for item in parallel_results if item is not None]

    @staticmethod
    def _should_serialize_version_generation() -> bool:
        return settings.is_sqlite_backend

    async def _generate_single_version_with_isolated_session(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        async with AsyncSessionLocal() as task_session:
            task_orchestrator = PipelineOrchestrator(task_session)
            return await task_orchestrator._generate_single_version(**kwargs)

    async def _generate_single_version(
        self,
        *,
        index: int,
        prompt_input: str,
        prompt_sections: List[Tuple[str, str]],
        writer_prompt: str,
        style_hint: Optional[str],
        project_id: str,
        chapter_number: int,
        outline_title: str,
        outline_summary: str,
        chapter_mission: Optional[dict],
        forbidden_characters: List[str],
        allowed_new_characters: List[str],
        user_id: int,
        writer_blueprint: Dict[str, Any],
        memory_context: Optional[str],
        enhanced_context: Optional[Dict[str, Any]],
        config: PipelineConfig,
        max_tokens: int = WRITER_GENERATION_MAX_TOKENS,
        target_word_count: int = DEFAULT_CHAPTER_TARGET_WORD_COUNT,
        content_rating: Optional[str] = None,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "chapter_mission": chapter_mission,
            "style_hint": style_hint,
            "pipeline": {"preset": config.preset},
        }

        content = ""
        if config.enable_preview:
            content, preview_meta = await self._generate_with_preview(
                project_id=project_id,
                chapter_number=chapter_number,
                outline_title=outline_title,
                outline_summary=outline_summary,
                writer_blueprint=writer_blueprint,
                memory_context=memory_context,
                style_hint=style_hint,
                enhanced_context=enhanced_context,
                user_id=user_id,
                target_word_count=target_word_count,
            )
            metadata["preview"] = preview_meta
            if not content and preview_meta.get("status") == "preview_evaluation_failed":
                raise RuntimeError("章节预览未通过质量评审，生成终止")

        if not content:
            content = await self._generate_with_gradient_retry(
                system_prompt=writer_prompt,
                user_content=prompt_input,
                prompt_sections=prompt_sections,
                style_hint=style_hint,
                user_id=user_id,
                max_tokens=max_tokens,
                content_rating=content_rating,
            )

        guardrail_result = self.guardrails.check(
            generated_text=content,
            forbidden_characters=forbidden_characters,
            allowed_new_characters=allowed_new_characters,
            pov=chapter_mission.get("pov") if chapter_mission else None,
        )
        guardrail_metadata = {"passed": guardrail_result.passed, "violations": []}

        if not guardrail_result.passed:
            guardrail_metadata["violations"] = [
                {"type": v.type, "severity": v.severity, "description": v.description}
                for v in guardrail_result.violations
            ]
            violations_text = self.guardrails.format_violations_for_rewrite(guardrail_result)
            content = await self._rewrite_with_guardrails(
                original_text=content,
                chapter_mission=chapter_mission,
                violations_text=violations_text,
                user_id=user_id,
            )

        parsed_json = None
        extracted_text = None
        try:
            parsed_json = json.loads(content)
            extracted_text = self._extract_text(parsed_json)
        except Exception:
            parsed_json = None

        metadata["guardrail"] = guardrail_metadata
        if parsed_json is not None:
            metadata["parsed_json"] = parsed_json

        return {
            "index": index,
            "content": extracted_text or content,
            "metadata": metadata,
        }

    async def _generate_with_gradient_retry(
        self,
        *,
        system_prompt: str,
        user_content: str,
        prompt_sections: Optional[List[Tuple[str, str]]] = None,
        style_hint: Optional[str] = None,
        user_id: int,
        max_tokens: int,
        content_rating: Optional[str] = None,
        base_temperature: float = 0.9,
        retry_budget_tokens: int = RETRY_REDUCED_CONTEXT_BUDGET,
    ) -> str:
        """LLM call with gradient retry: temperature bump → reduced context."""
        base_input = user_content
        if style_hint:
            base_input += f"\n\n[版本风格提示]\n{style_hint}"
        retry_inputs: List[Tuple[str, float, str]] = [
            (base_input, base_temperature, "base"),
            (base_input, min(base_temperature + 0.1, 1.0), "temp_bump"),
        ]
        if prompt_sections:
            reduced_sections = ContextBudgeter(total_budget_tokens=retry_budget_tokens).fit(prompt_sections)
            reduced_input = self._compose_prompt_input(reduced_sections, style_hint)
            if reduced_input != base_input:
                retry_inputs.append((reduced_input, min(base_temperature + 0.1, 1.0), "reduced_context"))

        last_exc: Optional[Exception] = None

        for attempt, (attempt_input, temp, attempt_mode) in enumerate(retry_inputs):
            try:
                response = await self.llm_service.get_llm_response(
                    system_prompt=system_prompt,
                    conversation_history=[{"role": "user", "content": attempt_input}],
                    temperature=temp,
                    user_id=user_id,
                    role="writer",
                    content_rating=content_rating,
                    timeout=600.0,
                    response_format=None,
                    max_tokens=max_tokens,
                )
                cleaned = remove_think_tags(response)
                content = unwrap_markdown_json(cleaned)
                if content and len(content) >= 100:
                    return content
                last_exc = HTTPException(
                    status_code=502,
                    detail="写作模型返回内容过短或为空，请重试",
                )
                logger.warning(
                    "Generation attempt %d (%s) returned short/empty content (%d chars), retrying",
                    attempt + 1,
                    attempt_mode,
                    len(content),
                )
            except Exception as exc:
                last_exc = exc
                logger.warning("Generation attempt %d (%s) failed: %s", attempt + 1, attempt_mode, exc)

        raise last_exc or HTTPException(status_code=502, detail="写作模型未返回有效内容，请重试")

    async def _generate_with_preview(
        self,
        *,
        project_id: str,
        chapter_number: int,
        outline_title: str,
        outline_summary: str,
        writer_blueprint: Dict[str, Any],
        memory_context: Optional[str],
        style_hint: Optional[str],
        enhanced_context: Optional[Dict[str, Any]],
        user_id: int,
        target_word_count: int = DEFAULT_CHAPTER_TARGET_WORD_COUNT,
    ) -> Tuple[str, Dict[str, Any]]:
        preview_service = PreviewGenerationService(self.session, self.llm_service, self.prompt_service)
        blueprint_context = json.dumps(writer_blueprint, ensure_ascii=False, indent=2)

        extra_constraints = []
        if enhanced_context:
            if enhanced_context.get("constitution"):
                extra_constraints.append(enhanced_context["constitution"])
            if enhanced_context.get("writer_persona"):
                extra_constraints.append(enhanced_context["writer_persona"])

        if extra_constraints:
            blueprint_context = blueprint_context + "\n\n" + "\n\n".join(extra_constraints)

        preview_result = await preview_service.generate_with_preview(
            project_id=project_id,
            chapter_number=chapter_number,
            outline={"title": outline_title, "summary": outline_summary},
            blueprint_context=blueprint_context,
            emotion_context="（无情绪曲线指导）",
            memory_context=memory_context or "（无记忆层上下文）",
            style_hint=style_hint or "",
            user_id=user_id,
            target_word_count=target_word_count,
        )

        return preview_result.get("full_chapter", ""), preview_result

    async def _rewrite_with_guardrails(
        self,
        *,
        original_text: str,
        chapter_mission: Optional[dict],
        violations_text: str,
        user_id: int,
    ) -> str:
        rewrite_prompt = await self.prompt_service.get_prompt("rewrite_guardrails")
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
            response = await self.llm_service.get_llm_response(
                system_prompt=rewrite_prompt,
                conversation_history=[{"role": "user", "content": rewrite_input}],
                temperature=0.3,
                user_id=user_id,
                timeout=300.0,
                response_format=None,
                max_tokens=WRITER_GENERATION_MAX_TOKENS,
            )
            cleaned = remove_think_tags(response)
            return cleaned
        except Exception as exc:
            logger.warning("自动修复失败，返回原文: %s", exc)
            return original_text

    @staticmethod
    def _extract_text(value: object) -> Optional[str]:
        if not value:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("content", "chapter_content", "chapter_text", "text", "body", "story"):
                if value.get(key):
                    nested = PipelineOrchestrator._extract_text(value.get(key))
                    if nested:
                        return nested
            return None
        if isinstance(value, list):
            for item in value:
                nested = PipelineOrchestrator._extract_text(item)
                if nested:
                    return nested
        return None

    async def _run_ai_review(
        self,
        *,
        versions: List[Dict[str, Any]],
        chapter_mission: Optional[dict],
        user_id: int,
        content_rating: Optional[str] = None,
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        if len(versions) <= 1:
            return 0, None

        contents = [v.get("content", "") for v in versions]
        try:
            ai_review_service = AIReviewService(self.llm_service, self.prompt_service)
            ai_review_result = await ai_review_service.review_versions(
                versions=contents,
                chapter_mission=chapter_mission,
                user_id=user_id,
                content_rating=content_rating,
            )
        except Exception as exc:
            logger.warning("AI 评审失败，跳过: %s", exc)
            return 0, None

        if not ai_review_result:
            return 0, None

        for idx, variant in enumerate(versions):
            variant.setdefault("metadata", {})["ai_review"] = {
                "is_best": idx == ai_review_result.best_version_index,
                "scores": ai_review_result.scores,
                "evaluation": ai_review_result.overall_evaluation if idx == ai_review_result.best_version_index else None,
                "flaws": ai_review_result.critical_flaws if idx == ai_review_result.best_version_index else None,
                "suggestions": ai_review_result.refinement_suggestions if idx == ai_review_result.best_version_index else None,
            }

        return ai_review_result.best_version_index, {
            "best_version_index": ai_review_result.best_version_index,
            "scores": ai_review_result.scores,
            "evaluation": ai_review_result.overall_evaluation,
            "flaws": ai_review_result.critical_flaws,
            "suggestions": ai_review_result.refinement_suggestions,
        }

    async def _run_self_critique(
        self,
        chapter_content: str,
        *,
        user_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        service = SelfCritiqueService(self.session, self.llm_service, self.prompt_service)
        critique = await service.critique_and_revise_loop(
            chapter_content=chapter_content,
            max_iterations=1,
            target_score=75.0,
            dimensions=[
                CritiqueDimension.LOGIC,
                CritiqueDimension.CHARACTER,
                CritiqueDimension.WRITING,
            ],
            context=context,
            user_id=user_id,
        )
        return critique.get("final_content", chapter_content), {
            "iterations": len(critique.get("iterations", [])),
            "final_score": critique.get("final_score", 0),
            "improvement": critique.get("improvement", 0),
            "status": critique.get("status", "unknown"),
        }

    async def _run_reader_simulation(
        self,
        chapter_content: str,
        *,
        chapter_number: int,
        previous_summary: Optional[str],
        user_id: int,
    ) -> Dict[str, Any]:
        service = ReaderSimulatorService(self.session, self.llm_service, self.prompt_service)
        return await service.simulate_reading_experience(
            chapter_content=chapter_content,
            chapter_number=chapter_number,
            reader_types=[ReaderType.THRILL_SEEKER, ReaderType.CRITIC, ReaderType.CASUAL],
            previous_summary=previous_summary,
            user_id=user_id,
        )

    async def _run_consistency_check(
        self,
        *,
        project_id: str,
        chapter_text: str,
        user_id: int,
    ) -> Tuple[str, Dict[str, Any]]:
        sync_session = getattr(self.session, "sync_session", self.session)
        service = ConsistencyService(sync_session, self.llm_service)
        result = await service.check_consistency(project_id, chapter_text, user_id, include_foreshadowing=True)
        report = {
            "is_consistent": result.is_consistent,
            "summary": result.summary,
            "check_time_ms": result.check_time_ms,
            "violations": [
                {
                    "severity": v.severity.value if hasattr(v.severity, "value") else v.severity,
                    "category": v.category,
                    "description": v.description,
                    "location": v.location,
                    "suggested_fix": v.suggested_fix,
                    "confidence": v.confidence,
                }
                for v in result.violations
            ],
        }

        needs_fix = any(
            v.severity in (ViolationSeverity.CRITICAL, ViolationSeverity.MAJOR)
            for v in result.violations
        )
        if needs_fix:
            fixed = await service.auto_fix(project_id, chapter_text, result.violations, user_id)
            if fixed:
                report["auto_fix_applied"] = True
                return fixed, report

        report["auto_fix_applied"] = False
        return chapter_text, report

    async def _run_optimizer(
        self,
        chapter_content: str,
        *,
        user_id: int,
        content_rating: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        prompt_map = {
            "dialogue": "optimize_dialogue",
            "environment": "optimize_environment",
            "psychology": "optimize_psychology",
            "rhythm": "optimize_rhythm",
        }

        optimized_content = chapter_content
        notes = []
        for dimension, prompt_name in prompt_map.items():
            prompt = await self.prompt_service.get_prompt(prompt_name)
            if not prompt:
                logger.warning("缺少优化提示词 %s，跳过 %s 维度", prompt_name, dimension)
                continue

            optimize_input = {
                "original_content": optimized_content,
                "additional_notes": "在不改变剧情走向的前提下优化该维度。",
            }
            try:
                response = await self.llm_service.get_llm_response(
                    system_prompt=prompt,
                    conversation_history=[{"role": "user", "content": json.dumps(optimize_input, ensure_ascii=False)}],
                    temperature=0.7,
                    user_id=user_id,
                    role="optimizer",
                    content_rating=content_rating,
                    timeout=600.0,
                )
                cleaned = remove_think_tags(response)
                normalized = unwrap_markdown_json(cleaned)
                try:
                    parsed = json.loads(normalized)
                    optimized_content = parsed.get("optimized_content", cleaned)
                    notes.append(
                        {
                            "dimension": dimension,
                            "notes": parsed.get("optimization_notes", "优化完成"),
                        }
                    )
                except json.JSONDecodeError:
                    optimized_content = cleaned
                    notes.append({"dimension": dimension, "notes": "优化完成（响应格式非标准JSON）"})
            except Exception as exc:
                logger.warning("优化维度 %s 失败: %s", dimension, exc)

        return optimized_content, {"steps": notes}

    async def _run_enrichment(
        self,
        chapter_content: str,
        *,
        user_id: int,
        target_word_count: int = 3000,
        content_rating: Optional[str] = None,
        target_ratio: float = 0.8,
        max_rounds: int = 3,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        if target_word_count <= 0:
            return chapter_content, None
        max_enrichment_attempts = min(max_rounds, 3)
        service = EnrichmentService(self.session, self.llm_service)
        current_content = self._sanitize_enrichment_output(chapter_content)
        original_word_count = self._count_text_length(current_content)
        current_word_count = original_word_count
        threshold_word_count = int(target_word_count * target_ratio)
        rounds = 0

        while current_word_count < threshold_word_count and rounds < max_enrichment_attempts:
            result = await service.check_and_enrich(
                chapter_text=current_content,
                target_word_count=target_word_count,
                user_id=user_id,
                content_rating=content_rating,
                threshold=target_ratio,
            )
            if not result:
                break

            candidate_content = self._sanitize_enrichment_output(result.enriched_content)
            candidate_word_count = self._count_text_length(candidate_content)
            rounds += 1

            if candidate_word_count <= current_word_count:
                break

            current_content = candidate_content
            current_word_count = candidate_word_count

        if current_word_count <= original_word_count:
            return chapter_content, None

        return current_content, {
            "original_word_count": original_word_count,
            "enriched_word_count": current_word_count,
            "enrichment_ratio": current_word_count / original_word_count if original_word_count > 0 else 1.0,
            "enrichment_type": "iterative",
            "rounds": rounds,
        }

    @staticmethod
    def _build_stage_flags(config: PipelineConfig) -> Dict[str, bool]:
        return {
            "preview": config.enable_preview,
            "optimizer": config.enable_optimizer,
            "consistency": config.enable_consistency,
            "enrichment": config.enable_enrichment is True,
            "constitution": config.enable_constitution,
            "persona": config.enable_persona,
            "six_dimension": config.enable_six_dimension,
            "reader_sim": config.enable_reader_sim,
            "self_critique": config.enable_self_critique,
            "memory": config.enable_memory,
            "rag": config.enable_rag,
            "rag_mode": config.rag_mode == "two_stage",
        }

    @staticmethod
    def _format_filtered_context(filtered: FilteredContext) -> Optional[str]:
        if not filtered:
            return None

        sections = []
        if filtered.plot_fuel:
            sections.append("## 情节燃料\n" + "\n".join(f"- {item}" for item in filtered.plot_fuel))
        if filtered.character_info:
            sections.append("## 人物维度\n" + "\n".join(f"- {item}" for item in filtered.character_info))
        if filtered.world_fragments:
            sections.append("## 世界碎片\n" + "\n".join(f"- {item}" for item in filtered.world_fragments))
        if filtered.narrative_techniques:
            sections.append("## 叙事技法\n" + "\n".join(f"- {item}" for item in filtered.narrative_techniques))
        if filtered.warnings:
            sections.append("## 冲突警告\n" + "\n".join(f"- {item}" for item in filtered.warnings))

        if not sections:
            return "（未检索到有效上下文）"

        return "\n\n".join(sections)


__all__ = ["PipelineOrchestrator", "PipelineConfig"]
