import uuid
from typing import Self

from pydantic import BaseModel

from venues.models.venue import Venue


class VenueDTO(BaseModel):
    id: uuid.UUID
    name: str
    city: str

    @classmethod
    def from_model(cls, venue: Venue) -> Self:
        return cls(id=venue.id, name=venue.name, city=venue.city)
