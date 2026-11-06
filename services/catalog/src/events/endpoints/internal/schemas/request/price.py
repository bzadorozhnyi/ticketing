import uuid

from pydantic import BaseModel, Field


class PriceSeatsIn(BaseModel):
    seat_ids: list[uuid.UUID] = Field(min_length=1)
