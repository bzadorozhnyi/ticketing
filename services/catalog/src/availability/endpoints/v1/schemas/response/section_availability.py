import uuid
from typing import Self

from pydantic import BaseModel, Field

from availability.dto.section_availability import SectionAvailabilityDTO


class SectionAvailabilityOut(BaseModel):
    event_id: uuid.UUID
    section_id: uuid.UUID
    taken_seat_ids: list[uuid.UUID] = Field(
        description="Seats sold or held, by row and number. Every other seat of the "
        "section's map is free. catalog's copy of seat-hold's state: it may lag a "
        "moment behind, and seat-hold decides every hold"
    )

    @classmethod
    def from_dto(cls, dto: SectionAvailabilityDTO) -> Self:
        return cls(
            event_id=dto.event_id,
            section_id=dto.section_id,
            taken_seat_ids=dto.taken_seat_ids,
        )
