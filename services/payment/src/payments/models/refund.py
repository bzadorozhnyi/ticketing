import uuid

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class Refund(BaseModel):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    charge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("charges.id"), unique=True)
