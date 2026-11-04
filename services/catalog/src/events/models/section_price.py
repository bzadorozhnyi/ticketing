import uuid

from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class SectionPrice(BaseModel):
    __tablename__ = "section_prices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "venue_id"], ["events.id", "events.venue_id"]
        ),
        ForeignKeyConstraint(
            ["section_id", "venue_id"], ["sections.id", "sections.venue_id"]
        ),
        CheckConstraint("price_minor >= 0", name="price_minor_non_negative"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    section_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    venue_id: Mapped[uuid.UUID]
    price_minor: Mapped[int]
