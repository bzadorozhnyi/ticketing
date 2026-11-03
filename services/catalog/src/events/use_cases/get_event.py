import uuid
from typing import Annotated

from fastapi import Depends

from core.exceptions import NotFoundException
from events.dto.event import EventDTO
from events.repositories.event import EventRepository, EventRepositoryDep


class GetEventUseCase:
    def __init__(self, *, event_repository: EventRepository):
        self.event_repository = event_repository

    async def execute(self, *, event_id: uuid.UUID) -> EventDTO:
        event = await self.event_repository.get_by_id(event_id=event_id)
        if event is None:
            raise NotFoundException(f"Event {event_id} not found")
        return EventDTO.from_model(event)


def get_get_event_use_case(event_repository: EventRepositoryDep) -> GetEventUseCase:
    return GetEventUseCase(event_repository=event_repository)


GetEventUseCaseDep = Annotated[GetEventUseCase, Depends(get_get_event_use_case)]
