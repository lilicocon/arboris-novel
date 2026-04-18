# AIMETA P=数据库初始化_创建表和默认数据|R=创建表_初始化管理员|NR=不含业务逻辑|E=init_db|X=internal|A=初始化函数|D=sqlalchemy|S=db|RD=./README.ai
import logging
import secrets
import string

from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ..core.config import settings
from ..core.security import hash_password
from ..models import Prompt, SystemConfig, User
from .base import Base
from .system_config_defaults import SYSTEM_CONFIG_DEFAULTS
from .session import AsyncSessionLocal, engine

logger = logging.getLogger(__name__)
LEGACY_SYSTEM_CONFIG_KEYS_TO_DELETE = (
    "updates.version_check_url",
)


async def init_db() -> None:
    """初始化数据库结构并确保默认管理员存在。"""

    await _ensure_database_exists()

    # ---- 第一步：创建所有表结构 ----
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表结构已初始化")
    await _ensure_schema_updates()

    # ---- 第二步：确保管理员账号至少存在一个 ----
    async with AsyncSessionLocal() as session:
        admin_exists = await session.execute(select(User).where(User.is_admin.is_(True)))
        if not admin_exists.scalars().first():
            logger.warning("未检测到管理员账号，正在创建默认管理员 ...")
            raw_password = settings.admin_default_password
            if not raw_password:
                alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
                raw_password = "".join(secrets.choice(alphabet) for _ in range(16))
                msg = (
                    f"\n{'='*60}\n"
                    f"  默认管理员随机密码（首次启动自动生成）\n"
                    f"  用户名: {settings.admin_default_username}\n"
                    f"  密  码: {raw_password}\n"
                    f"  请登录后立即修改！\n"
                    f"{'='*60}"
                )
                print(msg, flush=True)
                logger.warning("默认管理员密码已自动生成，请查看启动日志获取凭证")
            admin_user = User(
                username=settings.admin_default_username,
                email=settings.admin_default_email,
                hashed_password=hash_password(raw_password),
                is_admin=True,
            )

            session.add(admin_user)
            try:
                await session.commit()
                logger.info("默认管理员创建完成：%s", settings.admin_default_username)
            except IntegrityError:
                await session.rollback()
                logger.exception("默认管理员创建失败，可能是并发启动导致，请检查数据库状态")

        # ---- 第三步：清理已废弃的系统配置键 ----
        for legacy_key in LEGACY_SYSTEM_CONFIG_KEYS_TO_DELETE:
            legacy_config = await session.get(SystemConfig, legacy_key)
            if legacy_config:
                await session.delete(legacy_config)
                logger.info("已清理废弃系统配置键：%s", legacy_key)

        # ---- 第四步：同步系统配置到数据库 ----
        for entry in SYSTEM_CONFIG_DEFAULTS:
            value = entry.value_getter(settings)
            if value is None:
                continue
            existing = await session.get(SystemConfig, entry.key)
            if existing:
                if entry.key == "embedding.provider" and not (existing.value or "").strip():
                    existing.value = value
                if entry.description and existing.description != entry.description:
                    existing.description = entry.description
                continue
            session.add(
                SystemConfig(
                    key=entry.key,
                    value=value,
                    description=entry.description,
                )
            )

        await _ensure_default_prompts(session)

        await session.commit()


async def _ensure_database_exists() -> None:
    """在首次连接前确认数据库存在，针对不同驱动做最小化准备工作。"""
    url = make_url(settings.sqlalchemy_database_uri)

    if url.get_backend_name() == "sqlite":
        # SQLite 采用文件数据库，确保父目录存在即可，无需额外建库语句
        db_path = Path(url.database or "").expanduser()
        if not db_path.is_absolute():
            project_root = Path(__file__).resolve().parents[2]
            db_path = (project_root / db_path).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return

    database = (url.database or "").strip("/")
    if not database:
        return

    admin_url = URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=None,
        query=url.query,
    )

    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    async with admin_engine.begin() as conn:
        await conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{database}`"))
    await admin_engine.dispose()


async def _ensure_schema_updates() -> None:
    """补齐历史版本缺失的列，避免旧库在新版本报错。"""
    async with engine.begin() as conn:
        def _upgrade(sync_conn):
            inspector = inspect(sync_conn)
            table_names = set(inspector.get_table_names())

            if "chapter_outlines" in table_names:
                columns = {col["name"] for col in inspector.get_columns("chapter_outlines")}
                if "metadata" not in columns:
                    sync_conn.execute(text("ALTER TABLE chapter_outlines ADD COLUMN metadata JSON"))

            if "chapters" in table_names:
                chapter_columns = {col["name"] for col in inspector.get_columns("chapters")}
                if "generation_progress" not in chapter_columns:
                    sync_conn.execute(text("ALTER TABLE chapters ADD COLUMN generation_progress INTEGER DEFAULT 0"))
                if "generation_step" not in chapter_columns:
                    sync_conn.execute(text("ALTER TABLE chapters ADD COLUMN generation_step VARCHAR(64)"))
                if "generation_step_index" not in chapter_columns:
                    sync_conn.execute(text("ALTER TABLE chapters ADD COLUMN generation_step_index INTEGER DEFAULT 0"))
                if "generation_step_total" not in chapter_columns:
                    sync_conn.execute(text("ALTER TABLE chapters ADD COLUMN generation_step_total INTEGER DEFAULT 0"))
                if "generation_started_at" not in chapter_columns:
                    sync_conn.execute(text("ALTER TABLE chapters ADD COLUMN generation_started_at DATETIME"))

            if "llm_configs" in table_names:
                llm_columns = {col["name"] for col in inspector.get_columns("llm_configs")}
                if "embedding_provider_url" not in llm_columns:
                    sync_conn.execute(text("ALTER TABLE llm_configs ADD COLUMN embedding_provider_url TEXT"))
                if "embedding_provider_api_key" not in llm_columns:
                    sync_conn.execute(text("ALTER TABLE llm_configs ADD COLUMN embedding_provider_api_key TEXT"))
                if "embedding_provider_model" not in llm_columns:
                    sync_conn.execute(text("ALTER TABLE llm_configs ADD COLUMN embedding_provider_model TEXT"))
                if "embedding_provider_format" not in llm_columns:
                    sync_conn.execute(text("ALTER TABLE llm_configs ADD COLUMN embedding_provider_format TEXT"))
                sync_conn.execute(
                    text(
                        "UPDATE llm_configs "
                        "SET embedding_provider_format = 'openai' "
                        "WHERE embedding_provider_format IS NULL OR TRIM(embedding_provider_format) = ''"
                    )
                )

            if "novel_blueprints" in table_names:
                bp_columns = {col["name"] for col in inspector.get_columns("novel_blueprints")}
                if "chapter_length" not in bp_columns:
                    sync_conn.execute(text("ALTER TABLE novel_blueprints ADD COLUMN chapter_length INTEGER"))

            if "character_states" in table_names:
                cs_columns = {col["name"]: col for col in inspector.get_columns("character_states")}
                id_col = cs_columns.get("id")
                is_sqlite = str(sync_conn.engine.url.get_backend_name()) == "sqlite"
                if is_sqlite and id_col and str(id_col.get("type", "")).upper().startswith("BIG"):
                    sync_conn.execute(text(
                        "CREATE TABLE IF NOT EXISTS _character_states_backup AS SELECT * FROM character_states"
                    ))
                    sync_conn.execute(text("DROP TABLE character_states"))
                    sync_conn.execute(text(
                        "CREATE TABLE character_states ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "project_id VARCHAR(255) NOT NULL, "
                        "character_id INTEGER NOT NULL, "
                        "character_name VARCHAR(255) NOT NULL, "
                        "chapter_number INTEGER NOT NULL, "
                        "location VARCHAR(255), "
                        "location_detail TEXT, "
                        "emotion VARCHAR(64), "
                        "emotion_intensity INTEGER, "
                        "emotion_reason TEXT, "
                        "health_status VARCHAR(64), "
                        "injuries TEXT, "
                        "inventory TEXT, "
                        "inventory_changes TEXT, "
                        "relationship_changes TEXT, "
                        "power_level VARCHAR(64), "
                        "power_changes TEXT, "
                        "known_secrets TEXT, "
                        "new_knowledge TEXT, "
                        "current_goals TEXT, "
                        "goal_progress TEXT, "
                        "extra TEXT, "
                        "created_at DATETIME, "
                        "updated_at DATETIME)"
                    ))
                    sync_conn.execute(text(
                        "INSERT INTO character_states "
                        "SELECT * FROM _character_states_backup"
                    ))
                    sync_conn.execute(text("DROP TABLE _character_states_backup"))
                    for idx_sql in [
                        "CREATE INDEX IF NOT EXISTS ix_character_states_project_id ON character_states(project_id)",
                        "CREATE INDEX IF NOT EXISTS ix_character_states_character_id ON character_states(character_id)",
                        "CREATE INDEX IF NOT EXISTS ix_character_states_chapter_number ON character_states(chapter_number)",
                    ]:
                        sync_conn.execute(text(idx_sql))
                    logger.info("character_states.id 已从 BIGINT 修复为 INTEGER")

            # 修复 timeline_events / causal_chains / story_time_trackers 的 BIGINT 主键
            for table_name, columns_ddl in [
                ("timeline_events", (
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "project_id VARCHAR(255) NOT NULL, "
                    "chapter_number INTEGER NOT NULL, "
                    "story_time VARCHAR(255), "
                    "story_date VARCHAR(64), "
                    "time_elapsed VARCHAR(128), "
                    "event_type VARCHAR(64), "
                    "event_title VARCHAR(255) NOT NULL, "
                    "event_description TEXT, "
                    "involved_characters TEXT, "
                    "location VARCHAR(255), "
                    "caused_by_event_id INTEGER, "
                    "leads_to_event_ids TEXT, "
                    "importance INTEGER DEFAULT 5, "
                    "is_turning_point BOOLEAN DEFAULT 0, "
                    "extra TEXT, "
                    "created_at DATETIME"
                )),
                ("causal_chains", (
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "project_id VARCHAR(255) NOT NULL, "
                    "cause_type VARCHAR(64), "
                    "cause_description TEXT NOT NULL, "
                    "cause_chapter INTEGER NOT NULL, "
                    "effect_type VARCHAR(64), "
                    "effect_description TEXT NOT NULL, "
                    "effect_chapter INTEGER, "
                    "involved_characters TEXT, "
                    "cause_event_id INTEGER, "
                    "effect_event_id INTEGER, "
                    "status VARCHAR(32) DEFAULT 'pending', "
                    "resolution_description TEXT, "
                    "importance INTEGER DEFAULT 5, "
                    "extra TEXT, "
                    "created_at DATETIME, "
                    "updated_at DATETIME"
                )),
                ("story_time_trackers", (
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "project_id VARCHAR(255) NOT NULL UNIQUE, "
                    "time_system VARCHAR(64) DEFAULT 'modern', "
                    "start_date VARCHAR(64), "
                    "current_date VARCHAR(64), "
                    "current_time VARCHAR(64), "
                    "default_chapter_duration VARCHAR(64) DEFAULT '1 day', "
                    "chapter_time_map TEXT, "
                    "extra TEXT, "
                    "created_at DATETIME, "
                    "updated_at DATETIME"
                )),
            ]:
                if table_name in table_names:
                    cols = {col["name"]: col for col in inspector.get_columns(table_name)}
                    id_col = cols.get("id")
                    if is_sqlite and id_col and str(id_col.get("type", "")).upper().startswith("BIG"):
                        backup = f"_{table_name}_backup"
                        sync_conn.execute(text(
                            f"CREATE TABLE IF NOT EXISTS {backup} AS SELECT * FROM {table_name}"
                        ))
                        sync_conn.execute(text(f"DROP TABLE {table_name}"))
                        sync_conn.execute(text(
                            f"CREATE TABLE {table_name} ({columns_ddl})"
                        ))
                        sync_conn.execute(text(
                            f"INSERT INTO {table_name} SELECT * FROM {backup}"
                        ))
                        sync_conn.execute(text(f"DROP TABLE {backup}"))
                        logger.info(f"{table_name}.id 已从 BIGINT 修复为 INTEGER")
        await conn.run_sync(_upgrade)


async def _ensure_default_prompts(session: AsyncSession) -> None:
    prompts_dir = Path(__file__).resolve().parents[2] / "prompts"
    if not prompts_dir.is_dir():
        return

    result = await session.execute(select(Prompt.name, Prompt.content))
    existing: dict[str, str] = {row[0]: row[1] for row in result.all()}

    for prompt_file in sorted(prompts_dir.glob("*.md")):
        name = prompt_file.stem
        content = prompt_file.read_text(encoding="utf-8")
        if name not in existing:
            session.add(Prompt(name=name, content=content))
        elif existing[name] != content:
            result2 = await session.execute(select(Prompt).where(Prompt.name == name))
            prompt_row = result2.scalar_one_or_none()
            if prompt_row is not None:
                prompt_row.content = content
                logger.info("Updated prompt '%s' to match file content", name)
