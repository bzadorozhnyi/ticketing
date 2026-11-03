import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel

from events.models.event import Event
from venues.dto.venue import VenueDTO


class EventDTO(BaseModel):
    id: uuid.UUID
    title: str
    starts_at: datetime
    sales_start_at: datetime
    venue: VenueDTO

    @classmethod
    def from_model(cls, event: Event) -> Self:
        """`event.venue` must be eagerly loaded (the relationship is lazy="raise")."""
        return cls(
            id=event.id,
            title=event.title,
            starts_at=event.starts_at,
            sales_start_at=event.sales_start_at,
            venue=VenueDTO.from_model(event.venue),
        )
