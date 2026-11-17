import uuid

from fastapi import status

from availability.endpoints.v1.routes.router import router
from availability.endpoints.v1.schemas.response.section_availability import (
    SectionAvailabilityOut,
)
from availability.use_cases.get_section_availability import (
    GetSectionAvailabilityUseCaseDep,
)
from core.schemas import error_responses


@router.get(
    "/{event_id}/sections/{section_id}/availability",
    response_model=SectionAvailabilityOut,
    responses=error_responses(status.HTTP_404_NOT_FOUND),
)
async def get_section_availability(
    event_id: uuid.UUID,
    section_id: uuid.UUID,
    use_case: GetSectionAvailabilityUseCaseDep,
) -> SectionAvailabilityOut:
    dto = await use_case.execute(event_id=event_id, section_id=section_id)
    return SectionAvailabilityOut.from_dto(dto)
