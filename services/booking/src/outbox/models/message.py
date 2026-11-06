import uuid

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class OutboxMessage(BaseModel):
    """A message waiting for the relay. It is written in the same transaction as the
    state it belongs to, and deleted once the broker has confirmed it.
    """

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    queue: Mapped[str]
    body: Mapped[str]
    traceparent: Mapped[str | None]
