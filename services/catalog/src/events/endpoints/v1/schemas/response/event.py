import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel

from events.dto.event import EventDTO
from venues.dto.venue import VenueDTO


class EventVenueOut(BaseModel):
    id: uuid.UUID
    name: str
    city: str

    @classmethod
    def from_dto(cls, dto: VenueDTO) -> Self:
        return cls(id=dto.id, name=dto.name, city=dto.city)


class EventOut(BaseModel):
    id: uuid.UUID
    title: str
    starts_at: datetime
    sales_start_at: datetime
    currency: str
    venue: EventVenueOut

    @classmethod
    def from_dto(cls, dto: EventDTO) -> Self:
        return cls(
            id=dto.id,
            title=dto.title,
            starts_at=dto.starts_at,
            sales_start_at=dto.sales_start_at,
            currency=dto.currency,
            venue=EventVenueOut.from_dto(dto.venue),
        )


class EventListOut(BaseModel):
    items: list[EventOut]
    limit: int
    offset: int
