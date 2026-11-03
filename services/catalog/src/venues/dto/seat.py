import uuid
from typing import Self

from pydantic import BaseModel

from venues.models.seat import Seat


class SeatDTO(BaseModel):
    id: uuid.UUID
    row: int
    number: int

    @classmethod
    def from_model(cls, seat: Seat) -> Self:
        return cls(id=seat.id, row=seat.row, number=seat.number)
