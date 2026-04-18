import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.app.services.enrichment_service import EnrichmentResult
from backend.app.services.pipeline_orchestrator import PipelineOrchestrator


class PipelineOrchestratorAutoEnrichmentTest(unittest.TestCase):
    def test_prefers_ai_review_choice_when_it_meets_target(self) -> None:
        versions = [
            {"content": "甲" * 9200},
            {"content": "乙" * 10050},
        ]
        self.assertEqual(
            1,
            PipelineOrchestrator._select_preferred_version_index(
                versions=versions,
                ai_best_version_index=1,
                target_word_count=10000,
            ),
        )

    def test_switches_to_closest_version_that_reaches_target_when_ai_choice_is_too_short(self) -> None:
        versions = [
            {"content": "甲" * 7600},
            {"content": "乙" * 10080},
            {"content": "丙" * 11800},
        ]
        self.assertEqual(
            1,
            PipelineOrchestrator._select_preferred_version_index(
                versions=versions,
                ai_best_version_index=0,
                target_word_count=10000,
            ),
        )

    def test_falls_back_to_longest_version_when_no_version_reaches_target(self) -> None:
        versions = [
            {"content": "甲" * 7200},
            {"content": "乙" * 8100},
            {"content": "丙" * 7900},
        ]
        self.assertEqual(
            1,
            PipelineOrchestrator._select_preferred_version_index(
                versions=versions,
                ai_best_version_index=2,
                target_word_count=10000,
            ),
        )

    def test_auto_mode_runs_enrichment_when_draft_is_far_below_target(self) -> None:
        draft = "甲" * 6000
        self.assertTrue(
            PipelineOrchestrator._should_run_enrichment(
                chapter_content=draft,
                target_word_count=10000,
                configured_enrichment=None,
            )
        )

    def test_auto_mode_runs_enrichment_when_draft_is_below_eighty_percent_of_target(self) -> None:
        draft = "甲" * 7900
        self.assertTrue(
            PipelineOrchestrator._should_run_enrichment(
                chapter_content=draft,
                target_word_count=10000,
                configured_enrichment=None,
            )
        )

    def test_auto_mode_skips_enrichment_when_draft_reaches_eighty_percent_of_target(self) -> None:
        draft = "甲" * 8000
        self.assertFalse(
            PipelineOrchestrator._should_run_enrichment(
                chapter_content=draft,
                target_word_count=10000,
                configured_enrichment=None,
            )
        )

    def test_explicit_disable_skips_enrichment(self) -> None:
        draft = "甲" * 1000
        self.assertFalse(
            PipelineOrchestrator._should_run_enrichment(
                chapter_content=draft,
                target_word_count=10000,
                configured_enrichment=False,
            )
        )

    def test_explicit_enable_keeps_enrichment_enabled(self) -> None:
        draft = "甲" * 8000
        self.assertTrue(
            PipelineOrchestrator._should_run_enrichment(
                chapter_content=draft,
                target_word_count=10000,
                configured_enrichment=True,
            )
        )

    def test_sanitize_enrichment_output_removes_wrapper_copy(self) -> None:
        raw = """
以下是扩写后的完整章节内容（目标字数约10000字）：

---

正文示例内容。

（注：由于目标字数为10000字，本次扩写重点强化了感官描写与环境氛围。）

（本章完）

---

（实际字数约10250字，已严格遵循扩写原则。）
""".strip()

        self.assertEqual(
            "正文示例内容。",
            PipelineOrchestrator._sanitize_enrichment_output(raw),
        )


class PipelineOrchestratorEnrichmentExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_enrichment_retries_when_cleaned_content_is_still_short(self) -> None:
        orchestrator = object.__new__(PipelineOrchestrator)
        orchestrator.session = object()
        orchestrator.llm_service = object()

        fake_service = type("FakeService", (), {})()
        fake_service.check_and_enrich = AsyncMock(
            side_effect=[
                EnrichmentResult(
                    original_word_count=3000,
                    enriched_word_count=10000,
                    enriched_content=("甲" * 6000) + "\n\n（注：由于目标字数为10000字，本次扩写重点强化了感官描写与环境氛围。）",
                    enrichment_ratio=2.0,
                    enrichment_type="detail",
                ),
                EnrichmentResult(
                    original_word_count=6000,
                    enriched_word_count=9200,
                    enriched_content="乙" * 9200,
                    enrichment_ratio=1.53,
                    enrichment_type="detail",
                ),
            ]
        )

        with patch("backend.app.services.pipeline_orchestrator.EnrichmentService", return_value=fake_service):
            content, report = await PipelineOrchestrator._run_enrichment(
                orchestrator,
                "原始内容" * 1000,
                user_id=1,
                target_word_count=10000,
            )

        self.assertEqual("乙" * 9200, content)
        self.assertIsNotNone(report)
        self.assertEqual(2, fake_service.check_and_enrich.await_count)


class PipelineOrchestratorParallelVersionTest(unittest.IsolatedAsyncioTestCase):
    async def test_generate_versions_in_parallel_starts_all_tasks_before_waiting(self) -> None:
        orchestrator = object.__new__(PipelineOrchestrator)
        orchestrator.session = type("Session", (), {"commit": AsyncMock()})()
        chapter = type(
            "Chapter",
            (),
            {"generation_progress": 55, "generation_step": "draft_generation", "generation_step_index": 4},
        )()

        started = 0
        all_started = asyncio.Event()

        async def version_job(index: int) -> dict:
            nonlocal started
            started += 1
            if started == 2:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=0.2)
            return {"index": index, "content": f"正文{index}", "metadata": {}}

        def factory(index: int):
            return lambda: version_job(index)

        versions = await PipelineOrchestrator._generate_versions_in_parallel(
            orchestrator,
            chapter=chapter,
            version_factories=[factory(0), factory(1)],
        )

        self.assertEqual(["正文0", "正文1"], [item["content"] for item in versions])
        self.assertEqual(2, orchestrator.session.commit.await_count)


if __name__ == "__main__":
    unittest.main()
