import uuid
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class ChargeCommand(BaseModel):
    type: Literal["charge"] = "charge"
    order_id: uuid.UUID
    amount_minor: int
    currency: str
    payment_token: str


class RefundCommand(BaseModel):
    type: Literal["refund"] = "refund"
    order_id: uuid.UUID
    amount_minor: int
    currency: str


class ChargeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class RefundStatus(StrEnum):
    REFUNDED = "refunded"
    NOTHING_TO_REFUND = "nothing_to_refund"


class ChargeResult(BaseModel):
    type: Literal["charge_result"]
    order_id: uuid.UUID
    status: ChargeStatus


class RefundResult(BaseModel):
    type: Literal["refund_result"]
    order_id: uuid.UUID
    status: RefundStatus


Reply = ChargeResult | RefundResult
REPLY_ADAPTER: TypeAdapter[Reply] = TypeAdapter(
    Annotated[Reply, Field(discriminator="type")]
)
