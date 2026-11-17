import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from availability.dto.seat_event import SeatChangeDTO
from availability.models.seat_availability import SeatAvailability, SeatStatus
from core.db import SessionDep
from venues.models.seat import Seat


def is_taken() -> ColumnElement[bool]:
    """Sold, or held and not expired yet. A hold that runs out has no event, so its
    expiry is decided here, at read time.
    """
    return or_(
        SeatAvailability.status == SeatStatus.SOLD,
        and_(
            SeatAvailability.status == SeatStatus.HELD,
            SeatAvailability.expires_at > func.now(),
        ),
    )


class SeatAvailabilityRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def apply(
        self,
        *,
        event_id: uuid.UUID,
        status: SeatStatus,
        seats: list[SeatChangeDTO],
    ) -> int:
        """Writes each seat unless its row already has this version or a newer one.
        The check is part of the statement, so two runs at once cannot write an older
        state over a newer one. Returns how many seats changed.
        """
        statement = insert(SeatAvailability).values(
            [
                {
                    "event_id": event_id,
                    "seat_id": seat.seat_id,
                    "status": status,
                    "expires_at": seat.expires_at,
                    "version": seat.version,
                }
                for seat in seats
            ]
        )
        statement = statement.on_conflict_do_update(
            index_elements=[SeatAvailability.event_id, SeatAvailability.seat_id],
            set_={
                "status": statement.excluded.status,
                "expires_at": statement.excluded.expires_at,
                "version": statement.excluded.version,
            },
            where=SeatAvailability.version < statement.excluded.version,
        )
        result = await self.session.execute(
            statement.returning(SeatAvailability.seat_id)
        )
        return len(result.scalars().all())

    async def get_taken_in_section(
        self, *, event_id: uuid.UUID, section_id: uuid.UUID
    ) -> list[uuid.UUID]:
        result = await self.session.execute(
            select(SeatAvailability.seat_id)
            .join(Seat, Seat.id == SeatAvailability.seat_id)
            .where(
                SeatAvailability.event_id == event_id,
                Seat.section_id == section_id,
                is_taken(),
            )
            .order_by(Seat.row, Seat.number)
        )
        return list(result.scalars().all())

    async def count_taken_by_section(
        self, *, event_id: uuid.UUID
    ) -> dict[uuid.UUID, int]:
        result = await self.session.execute(
            select(Seat.section_id, func.count())
            .select_from(SeatAvailability)
            .join(Seat, Seat.id == SeatAvailability.seat_id)
            .where(SeatAvailability.event_id == event_id, is_taken())
            .group_by(Seat.section_id)
        )
        return dict(result.all())


def get_seat_availability_repository(session: SessionDep) -> SeatAvailabilityRepository:
    return SeatAvailabilityRepository(session=session)


SeatAvailabilityRepositoryDep = Annotated[
    SeatAvailabilityRepository, Depends(get_seat_availability_repository)
]
