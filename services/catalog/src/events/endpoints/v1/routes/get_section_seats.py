import uuid

from events.endpoints.v1.routes.router import router
from events.endpoints.v1.schemas.response.seat_map import SectionSeatsOut
from events.use_cases.get_section_seats import GetSectionSeatsUseCaseDep


@router.get("/{event_id}/sections/{section_id}/seats", response_model=SectionSeatsOut)
async def get_section_seats(
    event_id: uuid.UUID, section_id: uuid.UUID, use_case: GetSectionSeatsUseCaseDep
) -> SectionSeatsOut:
    dto = await use_case.execute(event_id=event_id, section_id=section_id)
    return SectionSeatsOut.from_dto(dto)
