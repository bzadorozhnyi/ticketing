from typing import Self

from pydantic import BaseModel

from events.dto.price import SeatsPriceDTO


class SeatsPriceOut(BaseModel):
    amount_minor: int
    currency: str

    @classmethod
    def from_dto(cls, dto: SeatsPriceDTO) -> Self:
        return cls(amount_minor=dto.amount_minor, currency=dto.currency)
