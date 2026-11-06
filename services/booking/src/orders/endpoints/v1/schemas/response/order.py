import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel

from orders.dto.order import OrderDTO


class OrderOut(BaseModel):
    id: uuid.UUID
    hold_id: uuid.UUID
    status: str
    failure_reason: str | None
    created_at: datetime

    @classmethod
    def from_dto(cls, dto: OrderDTO) -> Self:
        return cls(
            id=dto.id,
            hold_id=dto.hold_id,
            status=dto.status,
            failure_reason=dto.failure_reason,
            created_at=dto.created_at,
        )
