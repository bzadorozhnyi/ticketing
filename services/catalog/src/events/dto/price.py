from pydantic import BaseModel


class SeatsPriceDTO(BaseModel):
    amount_minor: int
    currency: str
