import uuid

from pydantic import BaseModel


class ExistingOrdersOut(BaseModel):
    order_ids: list[uuid.UUID]
