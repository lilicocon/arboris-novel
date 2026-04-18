import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.models.chapter_blueprint import ChapterBlueprint
from backend.app.models.novel import BlueprintCharacter
from backend.app.models.memory_layer import CharacterState
from backend.app.models.novel import NovelProject
from backend.app.models.project_memory import ChapterSnapshot, ProjectMemory
from backend.app.models.user import User
from backend.app.services.finalize_service import FinalizeService


class FinalizeServiceAsyncSessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "finalize-test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def test_finalize_chapter_works_with_async_session(self) -> None:
        async with self.session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="tester",
                    email="tester@example.com",
                    hashed_password="hashed",
                )
            )
            session.add(
                NovelProject(
                    id="project-1",
                    user_id=1,
                    title="测试项目",
                )
            )
            session.add(
                BlueprintCharacter(
                    project_id="project-1",
                    name="李杰",
                    identity="主角",
                    position=0,
                )
            )
            session.add(
                ChapterBlueprint(
                    project_id="project-1",
                    chapter_number=6,
                    brief_summary="测试章节",
                    is_finalized=False,
                )
            )
            await session.commit()

            service = FinalizeService(session, llm_service=AsyncMock(), vector_store_service=None)
            service._update_global_summary = AsyncMock(return_value="新的全局摘要")
            service._get_character_state_text = AsyncMock(return_value="")
            service._update_character_state = AsyncMock(return_value="角色状态")
            service._update_plot_arcs = AsyncMock(return_value={"main_conflicts": []})
            service._generate_chapter_summary = AsyncMock(return_value="章节摘要")

            result = await service.finalize_chapter(
                project_id="project-1",
                chapter_number=6,
                chapter_text="正文内容",
                user_id=1,
                skip_vector_update=True,
            )

            self.assertTrue(result["success"])

            project_memory = (
                await session.execute(
                    select(ProjectMemory).where(ProjectMemory.project_id == "project-1")
                )
            ).scalars().first()
            self.assertIsNotNone(project_memory)
            self.assertEqual("新的全局摘要", project_memory.global_summary)
            self.assertEqual(6, project_memory.last_updated_chapter)
            self.assertEqual(2, project_memory.version)

            snapshot = (
                await session.execute(
                    select(ChapterSnapshot).where(
                        ChapterSnapshot.project_id == "project-1",
                        ChapterSnapshot.chapter_number == 6,
                    )
                )
            ).scalars().first()
            self.assertIsNotNone(snapshot)
            self.assertEqual("章节摘要", snapshot.chapter_summary)

            state = (
                await session.execute(
                    select(CharacterState).where(
                        CharacterState.project_id == "project-1",
                        CharacterState.chapter_number == 6,
                    )
                )
            ).scalars().first()
            self.assertIsNotNone(state)
            self.assertEqual("__all__", state.character_name)

            blueprint = (
                await session.execute(
                    select(ChapterBlueprint).where(
                        ChapterBlueprint.project_id == "project-1",
                        ChapterBlueprint.chapter_number == 6,
                    )
                )
            ).scalars().first()
            self.assertIsNotNone(blueprint)
            self.assertTrue(blueprint.is_finalized)


if __name__ == "__main__":
    unittest.main()
