import uuid
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class ChargeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class Charge(BaseModel):
    """One row per order: `order_id` is the idempotency key. CANCELLED means a refund
    arrived before the charge, so a charge that arrives later must not take money.
    """

    __tablename__ = "charges"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="amount_minor_non_negative"),
        CheckConstraint(
            "status IN ('succeeded', 'declined', 'cancelled')", name="status_known"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    order_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str]
    status: Mapped[str]
