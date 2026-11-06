import uuid
from typing import Annotated

from fastapi import Depends

from core.exceptions import NotFoundException
from events.dto.price import SeatsPriceDTO
from events.repositories.event import EventRepository, EventRepositoryDep
from events.repositories.section_price import (
    SectionPriceRepository,
    SectionPriceRepositoryDep,
)


class PriceSeatsUseCase:
    def __init__(
        self,
        *,
        event_repository: EventRepository,
        section_price_repository: SectionPriceRepository,
    ):
        self.event_repository = event_repository
        self.section_price_repository = section_price_repository

    async def execute(
        self, *, event_id: uuid.UUID, seat_ids: list[uuid.UUID]
    ) -> SeatsPriceDTO:
        """The whole list or an error: a seat without a price must not make an order
        cheaper, so a seat of another venue or an unpriced section is refused.
        """
        event = await self.event_repository.get_by_id(event_id=event_id)
        if event is None:
            raise NotFoundException(f"Event {event_id} not found")

        prices = await self.section_price_repository.get_prices_by_seat(
            event_id=event_id, seat_ids=seat_ids
        )
        missing = [str(seat_id) for seat_id in seat_ids if seat_id not in prices]
        if missing:
            raise NotFoundException("Some seats have no price", details=missing)
        return SeatsPriceDTO(
            amount_minor=sum(prices[seat_id] for seat_id in seat_ids),
            currency=event.currency,
        )


def get_price_seats_use_case(
    event_repository: EventRepositoryDep,
    section_price_repository: SectionPriceRepositoryDep,
) -> PriceSeatsUseCase:
    return PriceSeatsUseCase(
        event_repository=event_repository,
        section_price_repository=section_price_repository,
    )


PriceSeatsUseCaseDep = Annotated[PriceSeatsUseCase, Depends(get_price_seats_use_case)]
