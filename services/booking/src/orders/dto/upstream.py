import uuid

from pydantic import BaseModel


class HoldDTO(BaseModel):
    event_id: uuid.UUID
    seat_ids: list[uuid.UUID]


class PriceDTO(BaseModel):
    amount_minor: int
    currency: str
