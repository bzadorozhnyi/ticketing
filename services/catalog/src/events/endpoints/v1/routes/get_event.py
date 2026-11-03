import uuid

from events.endpoints.v1.routes.router import router
from events.endpoints.v1.schemas.response.event import EventOut
from events.use_cases.get_event import GetEventUseCaseDep


@router.get("/{event_id}", response_model=EventOut)
async def get_event(event_id: uuid.UUID, use_case: GetEventUseCaseDep) -> EventOut:
    dto = await use_case.execute(event_id=event_id)
    return EventOut.from_dto(dto)
