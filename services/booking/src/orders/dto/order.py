import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel

from orders.models.order import Order


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
