import uuid
from typing import Annotated

from fastapi import Depends

from core.exceptions import NotFoundException
from events.dto.seat_map import EventSectionsDTO
from events.repositories.event import EventRepository, EventRepositoryDep
from venues.dto.section import SectionDTO
from venues.repositories.section import SectionRepository, SectionRepositoryDep


class ListEventSectionsUseCase:
    def __init__(
        self,
        *,
        event_repository: EventRepository,
        section_repository: SectionRepository,
    ):
        self.event_repository = event_repository
        self.section_repository = section_repository

    async def execute(self, *, event_id: uuid.UUID) -> EventSectionsDTO:
        event = await self.event_repository.get_by_id(event_id=event_id)
        if event is None:
            raise NotFoundException(f"Event {event_id} not found")

        rows = await self.section_repository.get_by_venue_with_seat_counts(
            venue_id=event.venue_id
        )
        return EventSectionsDTO(
            event_id=event.id,
            venue_id=event.venue_id,
            sections=[
                SectionDTO(id=section.id, name=section.name, seat_count=seat_count)
                for section, seat_count in rows
            ],
        )


def get_list_event_sections_use_case(
    event_repository: EventRepositoryDep,
    section_repository: SectionRepositoryDep,
) -> ListEventSectionsUseCase:
    return ListEventSectionsUseCase(
        event_repository=event_repository, section_repository=section_repository
    )


ListEventSectionsUseCaseDep = Annotated[
    ListEventSectionsUseCase, Depends(get_list_event_sections_use_case)
]
