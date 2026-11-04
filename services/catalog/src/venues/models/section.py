import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from core.db import BaseModel
from venues.models.venue import Venue


class Section(BaseModel):
    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint("venue_id", "name"),
        UniqueConstraint("id", "venue_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(255))

    venue: Mapped[Venue] = relationship(lazy="raise")
