import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.models.novel import ChapterOutline, NovelProject
from backend.app.models.user import User
from backend.app.services.novel_service import NovelService
from backend.app.services.outline_generation_service import OutlineGenerationService


class OutlineGenerationServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "outline-test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.temp_dir.cleanup()

    def test_detect_missing_ranges_compacts_consecutive_numbers(self) -> None:
        missing = OutlineGenerationService.detect_missing_ranges([1, 2, 5, 6, 10])
        self.assertEqual(
            [
                {"start_chapter": 3, "end_chapter": 4, "count": 2},
                {"start_chapter": 7, "end_chapter": 9, "count": 3},
            ],
            missing,
        )

    def test_validate_generated_batch_rejects_non_contiguous_numbers(self) -> None:
        with self.assertRaises(ValueError):
            OutlineGenerationService.validate_generated_batch(
                [
                    {"chapter_number": 1, "title": "一", "summary": "甲"},
                    {"chapter_number": 3, "title": "三", "summary": "乙"},
                ],
                start_chapter=1,
                num_chapters=2,
            )

    async def test_generate_range_batches_large_outline_requests(self) -> None:
        async with self.session_factory() as session:
            session.add(User(id=1, username="tester", email="tester@example.com", hashed_password="hashed"))
            session.add(NovelProject(id="project-1", user_id=1, title="测试项目"))
            await session.commit()

            service = OutlineGenerationService(
                session=session,
                prompt_service=AsyncMock(),
                llm_service=AsyncMock(),
            )
            service._request_outline_batch = AsyncMock(
                side_effect=[
                    [
                        {"chapter_number": num, "title": f"第{num}章", "summary": f"摘要{num}"}
                        for num in range(1, 21)
                    ],
                    [
                        {"chapter_number": num, "title": f"第{num}章", "summary": f"摘要{num}"}
                        for num in range(21, 26)
                    ],
                ]
            )

            await service.generate_and_persist_range(
                project_id="project-1",
                user_id=1,
                start_chapter=1,
                num_chapters=25,
            )

            result = await session.execute(
                select(ChapterOutline)
                .where(ChapterOutline.project_id == "project-1")
                .order_by(ChapterOutline.chapter_number)
            )
            outlines = result.scalars().all()
            self.assertEqual(list(range(1, 26)), [item.chapter_number for item in outlines])
            self.assertEqual(2, service._request_outline_batch.await_count)

    async def test_fill_missing_outlines_persists_detected_gap(self) -> None:
        async with self.session_factory() as session:
            session.add(User(id=1, username="tester", email="tester@example.com", hashed_password="hashed"))
            session.add(NovelProject(id="project-1", user_id=1, title="测试项目"))
            session.add_all(
                [
                    ChapterOutline(project_id="project-1", chapter_number=1, title="第1章", summary="摘要1"),
                    ChapterOutline(project_id="project-1", chapter_number=3, title="第3章", summary="摘要3"),
                    ChapterOutline(project_id="project-1", chapter_number=4, title="第4章", summary="摘要4"),
                ]
            )
            await session.commit()

            service = OutlineGenerationService(
                session=session,
                prompt_service=AsyncMock(),
                llm_service=AsyncMock(),
            )
            service._request_outline_batch = AsyncMock(
                return_value=[
                    {"chapter_number": 2, "title": "第2章", "summary": "摘要2"},
                ]
            )

            report = await service.fill_missing_outlines(project_id="project-1", user_id=1)

            result = await session.execute(
                select(ChapterOutline)
                .where(ChapterOutline.project_id == "project-1")
                .order_by(ChapterOutline.chapter_number)
            )
            outlines = result.scalars().all()
            self.assertEqual(list(range(1, 5)), [item.chapter_number for item in outlines])
            self.assertEqual(1, report["filled_chapters"])


if __name__ == "__main__":
    unittest.main()
