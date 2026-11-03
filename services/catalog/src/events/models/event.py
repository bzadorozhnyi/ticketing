import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from core.db import BaseModel
from venues.models.venue import Venue


class Event(BaseModel):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(index=True)
    sales_start_at: Mapped[datetime]

    venue: Mapped[Venue] = relationship(lazy="raise")
