# AIMETA P=配置服务_系统配置业务逻辑|R=配置读写|NR=不含数据访问|E=ConfigService|X=internal|A=服务类|D=sqlalchemy|S=db|RD=./README.ai
from typing import Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.system_config_repository import SystemConfigRepository
from ..models import SystemConfig
from ..schemas.config import SystemConfigCreate, SystemConfigRead, SystemConfigUpdate

WRITER_VERSION_KEYS = {"writer.chapter_versions", "writer.version_count"}
MIN_CHAPTER_VERSION_COUNT = 1
MAX_CHAPTER_VERSION_COUNT = 2


class ConfigService:
    """系统配置服务：提供 CRUD 接口，并负责转换 Pydantic 模型。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = SystemConfigRepository(session)

    def _normalize_config_value(self, key: str, value: str) -> str:
        if key in WRITER_VERSION_KEYS:
            try:
                parsed = int(str(value).strip())
            except (TypeError, ValueError):
                parsed = MIN_CHAPTER_VERSION_COUNT
            clamped = max(MIN_CHAPTER_VERSION_COUNT, min(MAX_CHAPTER_VERSION_COUNT, parsed))
            return str(clamped)
        return value

    async def list_configs(self) -> list[SystemConfigRead]:
        configs = await self.repo.list_all()
        return [SystemConfigRead.model_validate(cfg) for cfg in configs]

    async def get_config(self, key: str) -> Optional[SystemConfigRead]:
        config = await self.repo.get_by_key(key)
        return SystemConfigRead.model_validate(config) if config else None

    async def upsert_config(self, payload: SystemConfigCreate) -> SystemConfigRead:
        normalized_value = self._normalize_config_value(payload.key, payload.value)
        instance = await self.repo.get_by_key(payload.key)
        if instance:
            await self.repo.update_fields(instance, value=normalized_value, description=payload.description)
        else:
            instance = SystemConfig(**payload.model_dump(update={"value": normalized_value}))
            await self.repo.add(instance)
        await self.session.commit()
        return SystemConfigRead.model_validate(instance)

    async def patch_config(self, key: str, payload: SystemConfigUpdate) -> Optional[SystemConfigRead]:
        instance = await self.repo.get_by_key(key)
        if not instance:
            return None
        fields = payload.model_dump(exclude_unset=True)
        if "value" in fields and fields["value"] is not None:
            fields["value"] = self._normalize_config_value(key, fields["value"])
        await self.repo.update_fields(instance, **fields)
        await self.session.commit()
        return SystemConfigRead.model_validate(instance)

    async def remove_config(self, key: str) -> bool:
        instance = await self.repo.get_by_key(key)
        if not instance:
            return False
        await self.repo.delete(instance)
        await self.session.commit()
        return True
