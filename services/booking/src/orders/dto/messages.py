import uuid
from typing import Literal

from pydantic import BaseModel


class ChargeCommand(BaseModel):
    type: Literal["charge"] = "charge"
    order_id: uuid.UUID
    amount_minor: int
    currency: str
    payment_token: str
