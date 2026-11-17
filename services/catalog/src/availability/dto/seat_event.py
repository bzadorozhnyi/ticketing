import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class SeatEventType(StrEnum):
    SEATS_HELD = "SeatsHeld"
    HOLD_EXTENDED = "HoldExtended"
    SEATS_SOLD = "SeatsSold"
    SEATS_RELEASED = "SeatsReleased"


class SeatChangeDTO(BaseModel):
    seat_id: uuid.UUID
    version: int
    expires_at: datetime | None = None


class SeatEventDTO(BaseModel):
    """An event of seat-hold's exchange `seat-hold.events` (ADR 0014)."""

    type: SeatEventType
    event_id: uuid.UUID
    hold_id: uuid.UUID
    seats: list[SeatChangeDTO]
