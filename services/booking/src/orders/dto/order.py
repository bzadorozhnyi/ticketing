import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel

from orders.models.order import FailureReason, Order, OrderStatus, SagaStep


class OrderDTO(BaseModel):
    id: uuid.UUID
    hold_id: uuid.UUID
    status: str
    failure_reason: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, order: Order) -> Self:
        return cls(
            id=order.id,
            hold_id=order.hold_id,
            status=order.status,
            failure_reason=order.failure_reason,
            created_at=order.created_at,
        )


class FinishedOrder(BaseModel):
    """What a step returns when it ended the order; a repeat that changed nothing
    returns None instead."""

    status: OrderStatus
    failure_reason: FailureReason | None = None
    created_at: datetime


class CreatedOrderDTO(BaseModel):
    order: OrderDTO
    consistency_token: str


class SagaPosition(BaseModel):
    """Where a due order stood when a saga worker locked it."""

    step: SagaStep
    attempts: int
    next_attempt_at: datetime

    @classmethod
    def of(cls, order: Order) -> Self:
        return cls(
            step=order.saga_step,
            attempts=order.attempts,
            next_attempt_at=order.next_attempt_at,
        )
