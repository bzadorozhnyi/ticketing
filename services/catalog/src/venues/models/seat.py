import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from core.db import BaseModel
from venues.models.section import Section


class Seat(BaseModel):
    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("section_id", "row", "number"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sections.id"))
    row: Mapped[int]
    number: Mapped[int]

    section: Mapped[Section] = relationship(lazy="raise")
