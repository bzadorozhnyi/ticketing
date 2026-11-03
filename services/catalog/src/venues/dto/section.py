import uuid

from pydantic import BaseModel


class SectionDTO(BaseModel):
    id: uuid.UUID
    name: str
    seat_count: int
