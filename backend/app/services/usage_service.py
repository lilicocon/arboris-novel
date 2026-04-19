# AIMETA P=使用统计服务_API调用统计|R=统计记录_限额检查|NR=不含数据访问|E=UsageService|X=internal|A=服务类|D=sqlalchemy|S=db|RD=./README.ai
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import AsyncSessionLocal
from ..models.usage_metric import UsageMetric
from sqlalchemy import select

from ..repositories.usage_metric_repository import UsageMetricRepository

logger = logging.getLogger(__name__)


class UsageService:
    """通用计数服务，目前用于统计 API 请求次数等。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = UsageMetricRepository(session)

    async def increment(self, key: str) -> None:
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(UsageMetric).where(UsageMetric.key == key))
                counter = result.scalars().first()
                if counter is None:
                    counter = UsageMetric(key=key, value=0)
                    session.add(counter)
                    await session.flush()
                counter.value += 1
                await session.commit()
        except Exception as exc:
            logger.warning("写入 usage_metrics 失败，已忽略: %s", exc)

    async def get_value(self, key: str) -> int:
        counter = await self.repo.get_or_create(key)
        await self.session.commit()
        return counter.value
