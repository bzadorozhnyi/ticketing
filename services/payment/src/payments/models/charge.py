import uuid
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class ChargeStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class Charge(BaseModel):
    """One row per order: `order_id` is the idempotency key, here and at the provider.
    PENDING is written and committed before the provider is called, so a crash or a
    refund in between can see that the money may be taken. CANCELLED means a refund
    arrived before the charge, so a charge that arrives later must not take money.
    The token is kept to finish a pending charge with the same request.
    """

    __tablename__ = "charges"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="amount_minor_non_negative"),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'declined', 'cancelled')",
            name="status_known",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    order_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str]
    status: Mapped[str]
    payment_token: Mapped[str | None]
