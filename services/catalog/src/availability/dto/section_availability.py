import uuid

from pydantic import BaseModel


class SectionAvailabilityDTO(BaseModel):
    event_id: uuid.UUID
    section_id: uuid.UUID
    taken_seat_ids: list[uuid.UUID]
