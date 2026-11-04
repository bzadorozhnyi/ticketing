import uuid

from fastapi import status

from core.schemas import error_responses
from events.endpoints.v1.routes.router import router
from events.endpoints.v1.schemas.response.seat_map import EventSectionsOut
from events.use_cases.list_event_sections import ListEventSectionsUseCaseDep


@router.get(
    "/{event_id}/sections",
    response_model=EventSectionsOut,
    responses=error_responses(status.HTTP_404_NOT_FOUND),
)
async def list_event_sections(
    event_id: uuid.UUID, use_case: ListEventSectionsUseCaseDep
) -> EventSectionsOut:
    dto = await use_case.execute(event_id=event_id)
    return EventSectionsOut.from_dto(dto)
