import uuid

from pydantic import BaseModel, Field


class ExistingOrdersIn(BaseModel):
    order_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
