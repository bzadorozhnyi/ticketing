import uuid
from typing import Annotated

from fastapi import Depends

from core.exceptions import NotFoundException
from events.dto.seat_map import SectionSeatsDTO
from events.repositories.event import EventRepository, EventRepositoryDep
from venues.dto.seat import SeatDTO
from venues.repositories.seat import SeatRepository, SeatRepositoryDep
from venues.repositories.section import SectionRepository, SectionRepositoryDep


class GetSectionSeatsUseCase:
    def __init__(
        self,
        *,
        event_repository: EventRepository,
        section_repository: SectionRepository,
        seat_repository: SeatRepository,
    ):
        self.event_repository = event_repository
        self.section_repository = section_repository
        self.seat_repository = seat_repository

    async def execute(
        self, *, event_id: uuid.UUID, section_id: uuid.UUID
    ) -> SectionSeatsDTO:
        event = await self.event_repository.get_by_id(event_id=event_id)
        if event is None:
            raise NotFoundException(f"Event {event_id} not found")

        section = await self.section_repository.get_by_id(section_id=section_id)
        if section is None or section.venue_id != event.venue_id:
            raise NotFoundException(
                f"Section {section_id} not found for event {event_id}"
            )

        seats = await self.seat_repository.get_by_section(section_id=section_id)
        return SectionSeatsDTO(
            event_id=event.id,
            section_id=section.id,
            seats=[SeatDTO.from_model(seat) for seat in seats],
        )


def get_get_section_seats_use_case(
    event_repository: EventRepositoryDep,
    section_repository: SectionRepositoryDep,
    seat_repository: SeatRepositoryDep,
) -> GetSectionSeatsUseCase:
    return GetSectionSeatsUseCase(
        event_repository=event_repository,
        section_repository=section_repository,
        seat_repository=seat_repository,
    )


GetSectionSeatsUseCaseDep = Annotated[
    GetSectionSeatsUseCase, Depends(get_get_section_seats_use_case)
]
