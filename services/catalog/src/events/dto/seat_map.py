import uuid

from pydantic import BaseModel

from venues.dto.seat import SeatDTO
from venues.dto.section import SectionDTO


class EventSectionsDTO(BaseModel):
    event_id: uuid.UUID
    venue_id: uuid.UUID
    sections: list[SectionDTO]


class SectionSeatsDTO(BaseModel):
    event_id: uuid.UUID
    section_id: uuid.UUID
    seats: list[SeatDTO]
