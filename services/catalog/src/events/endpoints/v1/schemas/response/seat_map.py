import uuid
from typing import Self

from pydantic import BaseModel

from events.dto.seat_map import EventSectionsDTO, SectionSeatsDTO


class SectionOut(BaseModel):
    id: uuid.UUID
    name: str
    seat_count: int


class EventSectionsOut(BaseModel):
    event_id: uuid.UUID
    venue_id: uuid.UUID
    sections: list[SectionOut]

    @classmethod
    def from_dto(cls, dto: EventSectionsDTO) -> Self:
        return cls(
            event_id=dto.event_id,
            venue_id=dto.venue_id,
            sections=[
                SectionOut(id=s.id, name=s.name, seat_count=s.seat_count)
                for s in dto.sections
            ],
        )


class SeatOut(BaseModel):
    id: uuid.UUID
    row: int
    number: int


class SectionSeatsOut(BaseModel):
    event_id: uuid.UUID
    section_id: uuid.UUID
    seats: list[SeatOut]

    @classmethod
    def from_dto(cls, dto: SectionSeatsDTO) -> Self:
        return cls(
            event_id=dto.event_id,
            section_id=dto.section_id,
            seats=[SeatOut(id=s.id, row=s.row, number=s.number) for s in dto.seats],
        )
