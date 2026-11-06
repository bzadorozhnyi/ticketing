import uuid
from dataclasses import dataclass
from enum import StrEnum

from payments.models.charge import Charge


@dataclass(frozen=True, kw_only=True)
class ChargeRequest:
    order_id: uuid.UUID
    amount_minor: int
    currency: str
    payment_token: str


@dataclass(frozen=True, kw_only=True)
class ChargeDTO:
    charge_id: uuid.UUID
    order_id: uuid.UUID
    status: str

    @classmethod
    def from_model(cls, charge: Charge) -> ChargeDTO:
        return cls(charge_id=charge.id, order_id=charge.order_id, status=charge.status)


class RefundStatus(StrEnum):
    REFUNDED = "refunded"
    NOTHING_TO_REFUND = "nothing_to_refund"
