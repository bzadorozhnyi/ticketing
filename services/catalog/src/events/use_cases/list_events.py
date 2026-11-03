from typing import Annotated

from fastapi import Depends

from events.dto.event import EventDTO
from events.repositories.event import EventRepository, EventRepositoryDep


class ListEventsUseCase:
    def __init__(self, *, event_repository: EventRepository):
        self.event_repository = event_repository

    async def execute(self, *, limit: int, offset: int) -> list[EventDTO]:
        events = await self.event_repository.get_page(limit=limit, offset=offset)
        return [EventDTO.from_model(event) for event in events]


def get_list_events_use_case(event_repository: EventRepositoryDep) -> ListEventsUseCase:
    return ListEventsUseCase(event_repository=event_repository)


ListEventsUseCaseDep = Annotated[ListEventsUseCase, Depends(get_list_events_use_case)]
