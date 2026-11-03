from typing import Annotated

from fastapi import Query

from events.endpoints.v1.routes.router import router
from events.endpoints.v1.schemas.response.event import EventListOut, EventOut
from events.use_cases.list_events import ListEventsUseCaseDep

MAX_PAGE_SIZE = 100


@router.get("", response_model=EventListOut)
async def list_events(
    use_case: ListEventsUseCaseDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EventListOut:
    dtos = await use_case.execute(limit=limit, offset=offset)
    return EventListOut(
        items=[EventOut.from_dto(dto) for dto in dtos], limit=limit, offset=offset
    )
