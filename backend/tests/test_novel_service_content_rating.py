import tempfile
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.models.novel import NovelProject
from backend.app.models.user import User
from backend.app.schemas.novel import NovelSectionType
from backend.app.services.novel_service import NovelService


class NovelServiceContentRatingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "novel-content-rating.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def test_patch_blueprint_persists_content_rating_and_exposes_it_in_overview(self) -> None:
        async with self.session_factory() as session:
            session.add(User(id=1, username="tester", email="tester@example.com", hashed_password="hashed"))
            session.add(NovelProject(id="project-1", user_id=1, title="测试项目"))
            await session.commit()

            service = NovelService(session)
            await service.patch_blueprint("project-1", {"content_rating": "explicit"})

            project_schema = await service.get_project_schema("project-1", 1)
            self.assertEqual("explicit", project_schema.blueprint.content_rating)

            overview = await service.get_section_data("project-1", 1, NovelSectionType.OVERVIEW)
            self.assertEqual("explicit", overview.data["content_rating"])


if __name__ == "__main__":
    unittest.main()
