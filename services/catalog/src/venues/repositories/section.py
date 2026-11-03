import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from venues.models.seat import Seat
from venues.models.section import Section


class SectionRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def get_by_id(self, *, section_id: uuid.UUID) -> Section | None:
        return await self.session.get(Section, section_id)

    async def get_by_venue_with_seat_counts(
        self, *, venue_id: uuid.UUID
    ) -> list[tuple[Section, int]]:
        result = await self.session.execute(
            select(Section, func.count(Seat.id))
            .outerjoin(Seat, Seat.section_id == Section.id)
            .where(Section.venue_id == venue_id)
            .group_by(Section.id)
            .order_by(Section.name)
        )
        return [(section, seat_count) for section, seat_count in result.all()]


def get_section_repository(session: SessionDep) -> SectionRepository:
    return SectionRepository(session=session)


SectionRepositoryDep = Annotated[SectionRepository, Depends(get_section_repository)]
