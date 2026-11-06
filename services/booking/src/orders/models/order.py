import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class Order(BaseModel):
    """A hold has at most one active order. A failed one leaves the hold free,
    so the user can try again with another card.
    """

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index(
            "uq_orders_active_hold",
            "hold_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'confirmed')"),
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'failed')", name="status_known"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    user_id: Mapped[str]
    idempotency_key: Mapped[str]
    hold_id: Mapped[uuid.UUID]
    payment_token: Mapped[str]
    status: Mapped[str]
