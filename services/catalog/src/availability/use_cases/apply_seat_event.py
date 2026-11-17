from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from availability.dto.seat_event import SeatEventDTO, SeatEventType
from availability.models.seat_availability import SeatStatus
from availability.repositories.seat_availability import (
    SeatAvailabilityRepository,
    SeatAvailabilityRepositoryDep,
)
from core.db import SessionDep

STATUS_AFTER = {
    SeatEventType.SEATS_HELD: SeatStatus.HELD,
    SeatEventType.HOLD_EXTENDED: SeatStatus.HELD,
    SeatEventType.SEATS_SOLD: SeatStatus.SOLD,
    SeatEventType.SEATS_RELEASED: SeatStatus.FREE,
}


class ApplySeatEventUseCase:
    def __init__(
        self, *, session: AsyncSession, repository: SeatAvailabilityRepository
    ):
        self.session = session
        self.repository = repository

    async def execute(self, *, event: SeatEventDTO) -> int:
        """Safe to repeat and to run out of order: a seat changes only to a newer
        version. Returns how many seats changed.
        """
        if not event.seats:
            return 0
        changed = await self.repository.apply(
            event_id=event.event_id, status=STATUS_AFTER[event.type], seats=event.seats
        )
        await self.session.commit()
        return changed


def get_apply_seat_event_use_case(
    session: SessionDep, repository: SeatAvailabilityRepositoryDep
) -> ApplySeatEventUseCase:
    return ApplySeatEventUseCase(session=session, repository=repository)


ApplySeatEventUseCaseDep = Annotated[
    ApplySeatEventUseCase, Depends(get_apply_seat_event_use_case)
]
