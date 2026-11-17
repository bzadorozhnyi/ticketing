import uuid
from typing import Annotated

from fastapi import Depends

from availability.dto.section_availability import SectionAvailabilityDTO
from availability.repositories.seat_availability import (
    SeatAvailabilityRepository,
    SeatAvailabilityRepositoryDep,
)
from core.exceptions import NotFoundException
from events.repositories.event import EventRepository, EventRepositoryDep
from venues.repositories.section import SectionRepository, SectionRepositoryDep


class GetSectionAvailabilityUseCase:
    def __init__(
        self,
        *,
        event_repository: EventRepository,
        section_repository: SectionRepository,
        seat_availability_repository: SeatAvailabilityRepository,
    ):
        self.event_repository = event_repository
        self.section_repository = section_repository
        self.seat_availability_repository = seat_availability_repository

    async def execute(
        self, *, event_id: uuid.UUID, section_id: uuid.UUID
    ) -> SectionAvailabilityDTO:
        event = await self.event_repository.get_by_id(event_id=event_id)
        if event is None:
            raise NotFoundException(f"Event {event_id} not found")

        section = await self.section_repository.get_by_id(section_id=section_id)
        if section is None or section.venue_id != event.venue_id:
            raise NotFoundException(
                f"Section {section_id} not found for event {event_id}"
            )

        taken = await self.seat_availability_repository.get_taken_in_section(
            event_id=event.id, section_id=section.id
        )
        return SectionAvailabilityDTO(
            event_id=event.id, section_id=section.id, taken_seat_ids=taken
        )


def get_get_section_availability_use_case(
    event_repository: EventRepositoryDep,
    section_repository: SectionRepositoryDep,
    seat_availability_repository: SeatAvailabilityRepositoryDep,
) -> GetSectionAvailabilityUseCase:
    return GetSectionAvailabilityUseCase(
        event_repository=event_repository,
        section_repository=section_repository,
        seat_availability_repository=seat_availability_repository,
    )


GetSectionAvailabilityUseCaseDep = Annotated[
    GetSectionAvailabilityUseCase, Depends(get_get_section_availability_use_case)
]
