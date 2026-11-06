import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class SagaStep(StrEnum):
    STARTED = "started"
    AWAITING_CHARGE = "awaiting_charge"
    CHARGED = "charged"
    AWAITING_REFUND = "awaiting_refund"
    DONE = "done"


class FailureReason(StrEnum):
    HOLD_NOT_FOUND = "hold_not_found"
    HOLD_EXPIRED = "hold_expired"
    PRICE_NOT_FOUND = "price_not_found"
    CARD_DECLINED = "card_declined"
    REFUNDED_HOLD_LOST = "refunded_hold_lost"


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
        Index(
            "ix_orders_saga_due",
            "next_attempt_at",
            postgresql_where=text("saga_step IN ('started', 'charged')"),
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'failed')", name="status_known"
        ),
        CheckConstraint(
            "saga_step IN "
            "('started', 'awaiting_charge', 'charged', 'awaiting_refund', 'done')",
            name="saga_step_known",
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
    saga_step: Mapped[str] = mapped_column(server_default=SagaStep.STARTED)
    attempts: Mapped[int] = mapped_column(server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None]
    failure_reason: Mapped[str | None]
