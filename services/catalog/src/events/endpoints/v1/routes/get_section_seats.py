import uuid

from fastapi import Response, status

from core.cache.response_cache import ResponseCacheDep
from core.schemas import error_responses
from events.endpoints.v1.routes.router import router
from events.endpoints.v1.schemas.response.seat_map import SectionSeatsOut
from events.use_cases.get_section_seats import GetSectionSeatsUseCaseDep


@router.get(
    "/{event_id}/sections/{section_id}/seats",
    response_model=SectionSeatsOut,
    responses=error_responses(status.HTTP_404_NOT_FOUND),
)
async def get_section_seats(
    event_id: uuid.UUID,
    section_id: uuid.UUID,
    use_case: GetSectionSeatsUseCaseDep,
    cache: ResponseCacheDep,
) -> Response:
    async def load() -> bytes:
        dto = await use_case.execute(event_id=event_id, section_id=section_id)
        return SectionSeatsOut.from_dto(dto).model_dump_json().encode()

    body = await cache.get_or_load(
        key=f"section-seats:{event_id}:{section_id}", load=load
    )
    return Response(content=body, media_type="application/json")
