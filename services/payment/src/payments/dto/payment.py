import uuid
from enum import StrEnum
from typing import Self

from pydantic import BaseModel

from payments.models.charge import Charge


class ChargeRequest(BaseModel):
    order_id: uuid.UUID
    amount_minor: int
    currency: str
    payment_token: str


class ChargeDTO(BaseModel):
    charge_id: uuid.UUID
    order_id: uuid.UUID
    status: str

    @classmethod
    def from_model(cls, charge: Charge) -> Self:
        return cls(charge_id=charge.id, order_id=charge.order_id, status=charge.status)


class RefundStatus(StrEnum):
    REFUNDED = "refunded"
    NOTHING_TO_REFUND = "nothing_to_refund"
