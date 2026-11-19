import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SagaTimings(BaseModel):
    """Given at the start, so a replay uses the values its history saw."""

    reply_wait_seconds: float
    max_reply_wait_seconds: float
    give_up_after_seconds: float
    retry_base_delay_seconds: float
    max_retry_delay_seconds: float


class OrderInput(BaseModel):
    """Never the payment token: Temporal keeps a workflow's input in its history."""

    order_id: uuid.UUID
    hold_id: uuid.UUID
    user_id: str
    created_at: datetime
    timings: SagaTimings


class Reservation(BaseModel):
    """The sum to charge, or why the order cannot go on."""

    amount_minor: int = 0
    currency: str = ""
    failure_reason: str | None = None


class PaymentRequest(BaseModel):
    order_id: uuid.UUID
    amount_minor: int
    currency: str
    # Only a refund carries it: the reason the order fails with once the money is back.
    refund_reason: str | None = None


class Finish(BaseModel):
    order_id: uuid.UUID
    outcome: Literal["confirmed", "failed", "refunded"]
    failure_reason: str | None = None
