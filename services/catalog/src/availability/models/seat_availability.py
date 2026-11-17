import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class SeatStatus(StrEnum):
    HELD = "held"
    SOLD = "sold"
    FREE = "free"


class SeatAvailability(BaseModel):
    """catalog's copy of a seat's state in seat-hold, kept from its events.

    A released seat keeps its row as `free` with the release's version, so an older
    event that arrives later finds a newer version here and changes nothing. No foreign
    keys: seat-hold accepts any seat id, and its events must never fail on one catalog
    lacks.
    """

    __tablename__ = "seat_availability"
    __table_args__ = (
        CheckConstraint("status IN ('held', 'sold', 'free')", name="status_known"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    seat_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(4))
    expires_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(BigInteger)
