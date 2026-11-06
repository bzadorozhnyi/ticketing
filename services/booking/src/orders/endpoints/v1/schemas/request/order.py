import uuid

from pydantic import BaseModel, Field


class CreateOrderIn(BaseModel):
    hold_id: uuid.UUID
    payment_token: str = Field(min_length=1, max_length=255)
