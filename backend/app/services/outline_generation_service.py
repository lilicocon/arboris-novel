from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas.novel import ChapterOutline as ChapterOutlineSchema
from ..services.llm_service import LLMService
from ..services.novel_service import NovelService
from ..services.prompt_service import PromptService
from ..services.structured_llm_service import StructuredLLMService

logger = logging.getLogger(__name__)

DEFAULT_OUTLINE_BATCH_SIZE = 20
DEFAULT_EXPAND_SUMMARY_MIN_LENGTH = 80
DEFAULT_OUTLINE_STATUS = "draft"
_VALID_OUTLINE_STATUSES: frozenset[str] = frozenset({"draft", "approved", "needs_regen"})


class OutlineGenerationService:
    """章节大纲生成、补齐与扩写服务。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        prompt_service: PromptService,
        llm_service: LLMService,
        novel_service: Optional[NovelService] = None,
    ) -> None:
        self.session = session
        self.prompt_service = prompt_service
        self.llm_service = llm_service
        self.structured_llm_service = StructuredLLMService(llm_service)
        self.novel_service = novel_service or NovelService(session)

    @staticmethod
    def detect_missing_ranges(chapter_numbers: Sequence[int]) -> List[Dict[str, int]]:
        numbers = sorted({int(num) for num in chapter_numbers if int(num) > 0})
        if len(numbers) < 2:
            return []

        missing_ranges: List[Dict[str, int]] = []
        range_start: Optional[int] = None
        previous = numbers[0]
        for current in numbers[1:]:
            if current - previous > 1:
                if range_start is None:
                    range_start = previous + 1
                range_end = current - 1
                missing_ranges.append(
                    {
                        "start_chapter": range_start,
                        "end_chapter": range_end,
                        "count": range_end - range_start + 1,
                    }
                )
                range_start = None
            previous = current
        return missing_ranges

    @classmethod
    def detect_missing_ranges_from_outlines(
        cls,
        outlines: Iterable[ChapterOutlineSchema | Dict[str, Any]],
    ) -> List[Dict[str, int]]:
        chapter_numbers = []
        for item in outlines:
            if isinstance(item, ChapterOutlineSchema):
                chapter_numbers.append(item.chapter_number)
            else:
                chapter_numbers.append(int(item.get("chapter_number", 0)))
        return cls.detect_missing_ranges(chapter_numbers)

    @staticmethod
    def _normalize_outline_item(item: Dict[str, Any]) -> Dict[str, Any]:
        chapter_number = int(item["chapter_number"])
        title = str(item.get("title", "")).strip()
        summary = str(item.get("summary", "")).strip()
        if not title:
            raise ValueError(f"章节 {chapter_number} 缺少标题")
        if not summary:
            raise ValueError(f"章节 {chapter_number} 缺少摘要")
        raw_status = str(item.get("status") or DEFAULT_OUTLINE_STATUS)
        return {
            "chapter_number": chapter_number,
            "title": title,
            "summary": summary,
            "status": raw_status if raw_status in _VALID_OUTLINE_STATUSES else DEFAULT_OUTLINE_STATUS,
        }

    @classmethod
    def validate_generated_batch(
        cls,
        outlines: Sequence[Dict[str, Any]],
        *,
        start_chapter: int,
        num_chapters: int,
        expected_numbers: Optional[Sequence[int]] = None,
    ) -> List[Dict[str, Any]]:
        if len(outlines) != num_chapters:
            raise ValueError(f"AI 返回章节数不正确，期望 {num_chapters}，实际 {len(outlines)}")

        normalized = [cls._normalize_outline_item(item) for item in outlines]
        normalized.sort(key=lambda item: item["chapter_number"])
        actual_numbers = [item["chapter_number"] for item in normalized]
        if expected_numbers is None:
            expected = list(range(start_chapter, start_chapter + num_chapters))
        else:
            expected = list(expected_numbers)

        if actual_numbers != expected:
            raise ValueError(
                f"AI 返回章节编号不连续，期望 {expected[0]}-{expected[-1]}，实际 {actual_numbers}"
            )
        return normalized

    async def generate_and_persist_range(
        self,
        *,
        project_id: str,
        user_id: int,
        start_chapter: int,
        num_chapters: int,
        batch_size: int = DEFAULT_OUTLINE_BATCH_SIZE,
    ) -> Dict[str, int]:
        if num_chapters <= 0:
            return {"generated_chapters": 0, "batches": 0}

        generated = 0
        batches = 0
        current_start = start_chapter
        remaining = num_chapters

        while remaining > 0:
            current_batch = min(batch_size, remaining)
            current_outlines = await self._list_outline_dicts(project_id)
            batch = await self._request_outline_batch(
                project_id=project_id,
                user_id=user_id,
                current_outlines=current_outlines,
                start_chapter=current_start,
                num_chapters=current_batch,
            )
            for item in batch:
                await self.novel_service.update_or_create_outline(
                    project_id=project_id,
                    chapter_number=item["chapter_number"],
                    title=item["title"],
                    summary=item["summary"],
                    metadata={"status": DEFAULT_OUTLINE_STATUS},
                )
            await self.session.commit()
            generated += len(batch)
            batches += 1
            current_start += current_batch
            remaining -= current_batch

        await self.novel_service._touch_project(project_id)
        return {"generated_chapters": generated, "batches": batches}

    async def fill_missing_outlines(
        self,
        *,
        project_id: str,
        user_id: int,
        batch_size: int = DEFAULT_OUTLINE_BATCH_SIZE,
    ) -> Dict[str, Any]:
        current_outlines = await self._list_outline_dicts(project_id)
        missing_ranges = self.detect_missing_ranges_from_outlines(current_outlines)
        if not missing_ranges:
            return {"filled_chapters": 0, "filled_ranges": 0, "missing_ranges": []}

        filled = 0
        for item in missing_ranges:
            report = await self.generate_and_persist_range(
                project_id=project_id,
                user_id=user_id,
                start_chapter=item["start_chapter"],
                num_chapters=item["count"],
                batch_size=batch_size,
            )
            filled += report["generated_chapters"]

        return {
            "filled_chapters": filled,
            "filled_ranges": len(missing_ranges),
            "missing_ranges": missing_ranges,
        }

    async def expand_existing_outlines(
        self,
        *,
        project_id: str,
        user_id: int,
        start_chapter: Optional[int] = None,
        end_chapter: Optional[int] = None,
        batch_size: int = DEFAULT_OUTLINE_BATCH_SIZE,
        min_summary_length: int = DEFAULT_EXPAND_SUMMARY_MIN_LENGTH,
    ) -> Dict[str, int]:
        current_outlines = await self._list_outline_dicts(project_id)
        candidates = [
            item
            for item in current_outlines
            if (start_chapter is None or item["chapter_number"] >= start_chapter)
            and (end_chapter is None or item["chapter_number"] <= end_chapter)
            and len(item["summary"]) < min_summary_length
        ]
        if not candidates:
            return {"expanded_chapters": 0, "batches": 0}

        expanded = 0
        batches = 0
        for index in range(0, len(candidates), batch_size):
            batch = candidates[index : index + batch_size]
            current_outlines = await self._list_outline_dicts(project_id)
            expanded_batch = await self._request_expand_batch(
                project_id=project_id,
                user_id=user_id,
                current_outlines=current_outlines,
                target_outlines=batch,
            )
            for item in expanded_batch:
                await self.novel_service.update_or_create_outline(
                    project_id=project_id,
                    chapter_number=item["chapter_number"],
                    title=item["title"],
                    summary=item["summary"],
                    metadata={"status": item.get("status") or DEFAULT_OUTLINE_STATUS},
                )
            await self.session.commit()
            expanded += len(expanded_batch)
            batches += 1

        await self.novel_service._touch_project(project_id)
        return {"expanded_chapters": expanded, "batches": batches}

    async def update_outline_status(
        self,
        *,
        project_id: str,
        chapter_numbers: Sequence[int],
        status: str,
    ) -> Dict[str, int]:
        current_outlines = await self._list_outline_dicts(project_id)
        numbers = {int(num) for num in chapter_numbers if int(num) > 0}
        updated = 0
        for item in current_outlines:
            if item["chapter_number"] not in numbers:
                continue
            await self.novel_service.update_or_create_outline(
                project_id=project_id,
                chapter_number=item["chapter_number"],
                title=item["title"],
                summary=item["summary"],
                metadata={"status": status},
            )
            updated += 1
        if updated:
            await self.session.commit()
            await self.novel_service._touch_project(project_id)
        return {"updated_chapters": updated}

    async def reroll_outlines(
        self,
        *,
        project_id: str,
        user_id: int,
        chapter_numbers: Optional[Sequence[int]] = None,
        batch_size: int = DEFAULT_OUTLINE_BATCH_SIZE,
        only_needs_regen: bool = True,
    ) -> Dict[str, Any]:
        current_outlines = await self._list_outline_dicts(project_id)
        if chapter_numbers:
            requested = {int(num) for num in chapter_numbers if int(num) > 0}
            targets = [item for item in current_outlines if item["chapter_number"] in requested]
        elif only_needs_regen:
            targets = [item for item in current_outlines if item.get("status") == "needs_regen"]
        else:
            targets = list(current_outlines)

        if not targets:
            return {"rerolled_chapters": 0, "batches": 0}

        rerolled = 0
        batches = 0
        for index in range(0, len(targets), batch_size):
            batch_targets = targets[index : index + batch_size]
            latest_outlines = await self._list_outline_dicts(project_id)
            regenerated = await self._request_reroll_batch(
                project_id=project_id,
                user_id=user_id,
                current_outlines=latest_outlines,
                target_outlines=batch_targets,
            )
            for item in regenerated:
                await self.novel_service.update_or_create_outline(
                    project_id=project_id,
                    chapter_number=item["chapter_number"],
                    title=item["title"],
                    summary=item["summary"],
                    metadata={"status": DEFAULT_OUTLINE_STATUS},
                )
            await self.session.commit()
            rerolled += len(regenerated)
            batches += 1

        await self.novel_service._touch_project(project_id)
        return {"rerolled_chapters": rerolled, "batches": batches}

    async def _request_outline_batch(
        self,
        *,
        project_id: str,
        user_id: int,
        current_outlines: Sequence[Dict[str, Any]],
        start_chapter: int,
        num_chapters: int,
    ) -> List[Dict[str, Any]]:
        outline_prompt = await self.prompt_service.get_prompt("outline_generation")
        if not outline_prompt:
            raise ValueError("未配置大纲生成提示词")

        project = await self.novel_service.repo.get_by_id(project_id)
        if not project:
            raise ValueError("项目不存在")
        project_schema = await self.novel_service._serialize_project(project)
        content_rating = getattr(project_schema.blueprint, "content_rating", "safe")
        _bp = project_schema.blueprint
        blueprint_text = json.dumps(_bp.model_dump() if _bp else {}, ensure_ascii=False, indent=2)
        context_text = self._build_outline_context(
            current_outlines=current_outlines,
            start_chapter=start_chapter,
            end_chapter=start_chapter + num_chapters - 1,
        )
        prompt_input = f"""
[世界蓝图]
{blueprint_text}

[相邻章节参考]
{context_text}

[生成任务]
请生成第 {start_chapter} 章到第 {start_chapter + num_chapters - 1} 章的大纲，共 {num_chapters} 章。

硬性要求：
1. 必须返回恰好 {num_chapters} 个章节。
2. chapter_number 必须从 {start_chapter} 连续递增到 {start_chapter + num_chapters - 1}，不能跳号，不能缺失，不能额外生成。
3. 只能返回一个 JSON 对象，结构为 {{ "chapters": [...] }}。
4. 每个 chapter 必须包含 chapter_number、title、summary。
5. summary 必须写具体剧情推进，不要只写一句空泛描述。
6. 不要改动参考章节编号，它们只作为上下文。
"""
        data = await self.structured_llm_service.generate_json(
            system_prompt=outline_prompt,
            user_content=prompt_input,
            temperature=0.7,
            user_id=user_id,
            role="writer",
            content_rating=content_rating,
        )
        if not isinstance(data.get("chapters"), list) or len(data["chapters"]) == 0:
            raise ValueError("LLM returned outline without valid chapters field")
        return self.validate_generated_batch(
            data["chapters"],
            start_chapter=start_chapter,
            num_chapters=num_chapters,
        )

    async def _request_expand_batch(
        self,
        *,
        project_id: str,
        user_id: int,
        current_outlines: Sequence[Dict[str, Any]],
        target_outlines: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        outline_prompt = await self.prompt_service.get_prompt("outline_generation")
        if not outline_prompt:
            raise ValueError("未配置大纲生成提示词")

        project = await self.novel_service.repo.get_by_id(project_id)
        if not project:
            raise ValueError("项目不存在")
        project_schema = await self.novel_service._serialize_project(project)
        content_rating = getattr(project_schema.blueprint, "content_rating", "safe")
        _bp = project_schema.blueprint
        blueprint_text = json.dumps(_bp.model_dump() if _bp else {}, ensure_ascii=False, indent=2)
        chapter_numbers = [item["chapter_number"] for item in target_outlines]
        prompt_input = f"""
[世界蓝图]
{blueprint_text}

[相邻章节参考]
{self._build_outline_context(current_outlines=current_outlines, start_chapter=min(chapter_numbers), end_chapter=max(chapter_numbers))}

[待扩写章节]
{json.dumps(target_outlines, ensure_ascii=False, indent=2)}

[扩写任务]
请在不改变 chapter_number 和 title 的前提下，扩写这些章节的 summary，让剧情推进、冲突、转折和情感张力更清楚。

硬性要求：
1. 必须返回恰好 {len(target_outlines)} 个章节。
2. chapter_number 必须保持为 {chapter_numbers}。
3. title 必须保持不变。
4. 只能扩写 summary，不要删除剧情信息。
5. 只能返回一个 JSON 对象，结构为 {{ "chapters": [...] }}。
"""
        data = await self.structured_llm_service.generate_json(
            system_prompt=outline_prompt,
            user_content=prompt_input,
            temperature=0.5,
            user_id=user_id,
            role="writer",
            content_rating=content_rating,
        )
        validated = self.validate_generated_batch(
            data.get("chapters", []),
            start_chapter=min(chapter_numbers),
            num_chapters=len(target_outlines),
            expected_numbers=chapter_numbers,
        )
        original_titles = {item["chapter_number"]: item["title"] for item in target_outlines}
        for item in validated:
            item["title"] = original_titles[item["chapter_number"]]
        return validated

    async def _request_reroll_batch(
        self,
        *,
        project_id: str,
        user_id: int,
        current_outlines: Sequence[Dict[str, Any]],
        target_outlines: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        outline_prompt = await self.prompt_service.get_prompt("outline_generation")
        if not outline_prompt:
            raise ValueError("未配置大纲生成提示词")

        project = await self.novel_service.repo.get_by_id(project_id)
        if not project:
            raise ValueError("项目不存在")
        project_schema = await self.novel_service._serialize_project(project)
        content_rating = getattr(project_schema.blueprint, "content_rating", "safe")
        _bp = project_schema.blueprint
        blueprint_text = json.dumps(_bp.model_dump() if _bp else {}, ensure_ascii=False, indent=2)
        chapter_numbers = [item["chapter_number"] for item in target_outlines]
        prompt_input = f"""
[世界蓝图]
{blueprint_text}

[完整章节参考]
{self._build_outline_context(current_outlines=current_outlines, start_chapter=min(chapter_numbers), end_chapter=max(chapter_numbers))}

[待重生成章节]
{json.dumps(target_outlines, ensure_ascii=False, indent=2)}

[重生成任务]
请只重写以上章节的 title 和 summary，让情节更有张力、推进更明确、章节钩子更强。

硬性要求：
1. 只能返回一个 JSON 对象，结构为 {{ "chapters": [...] }}。
2. 必须返回恰好 {len(target_outlines)} 个章节。
3. chapter_number 必须严格保持为 {chapter_numbers}。
4. 不要输出参考章节，不要新增其它章节。
5. 允许重写 title 和 summary，但不能改动章节编号。
"""
        data = await self.structured_llm_service.generate_json(
            system_prompt=outline_prompt,
            user_content=prompt_input,
            temperature=0.8,
            user_id=user_id,
            role="writer",
            content_rating=content_rating,
        )
        validated = self.validate_generated_batch(
            data.get("chapters", []),
            start_chapter=min(chapter_numbers),
            num_chapters=len(target_outlines),
            expected_numbers=chapter_numbers,
        )
        return validated

    async def _list_outline_dicts(self, project_id: str) -> List[Dict[str, Any]]:
        project = await self.novel_service.repo.get_by_id(project_id)
        if not project:
            return []
        return [
            {
                "chapter_number": item.chapter_number,
                "title": item.title,
                "summary": item.summary or "",
                "status": ((item.metadata or {}).get("status") or DEFAULT_OUTLINE_STATUS),
            }
            for item in sorted(project.outlines, key=lambda outline: outline.chapter_number)
        ]

    @staticmethod
    def _build_outline_context(
        *,
        current_outlines: Sequence[Dict[str, Any]],
        start_chapter: int,
        end_chapter: int,
        window: int = 3,
    ) -> str:
        if not current_outlines:
            return "暂无"

        selected = [
            item
            for item in current_outlines
            if start_chapter - window <= item["chapter_number"] <= end_chapter + window
        ]
        if not selected:
            selected = list(current_outlines[-window:])

        lines = [
            f"第{item['chapter_number']}章 - {item['title']}: {item['summary']}"
            for item in selected
        ]
        return "\n".join(lines) if lines else "暂无"


__all__ = ["OutlineGenerationService", "DEFAULT_OUTLINE_BATCH_SIZE", "DEFAULT_EXPAND_SUMMARY_MIN_LENGTH"]
