import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from events.models.section_price import SectionPrice


class SectionPriceRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def get_prices_by_section(
        self, *, event_id: uuid.UUID
    ) -> dict[uuid.UUID, int]:
        result = await self.session.execute(
            select(SectionPrice.section_id, SectionPrice.price_minor).where(
                SectionPrice.event_id == event_id
            )
        )
        return dict(result.all())


def get_section_price_repository(session: SessionDep) -> SectionPriceRepository:
    return SectionPriceRepository(session=session)


SectionPriceRepositoryDep = Annotated[
    SectionPriceRepository, Depends(get_section_price_repository)
]
