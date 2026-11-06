import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from payments.dto.payment import RefundStatus


class ChargeCommand(BaseModel):
    type: Literal["charge"]
    order_id: uuid.UUID
    amount_minor: int
    currency: str
    payment_token: str


class RefundCommand(BaseModel):
    type: Literal["refund"]
    order_id: uuid.UUID
    amount_minor: int
    currency: str


class ChargeResult(BaseModel):
    type: Literal["charge_result"] = "charge_result"
    order_id: uuid.UUID
    charge_id: uuid.UUID
    status: str


class RefundResult(BaseModel):
    type: Literal["refund_result"] = "refund_result"
    order_id: uuid.UUID
    status: RefundStatus


Command = ChargeCommand | RefundCommand
COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(
    Annotated[Command, Field(discriminator="type")]
)
