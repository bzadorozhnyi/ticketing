import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from venues.models.seat import Seat


class SeatRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def get_by_section(self, *, section_id: uuid.UUID) -> list[Seat]:
        result = await self.session.execute(
            select(Seat)
            .where(Seat.section_id == section_id)
            .order_by(Seat.row, Seat.number)
        )
        return list(result.scalars().all())


def get_seat_repository(session: SessionDep) -> SeatRepository:
    return SeatRepository(session=session)


SeatRepositoryDep = Annotated[SeatRepository, Depends(get_seat_repository)]
