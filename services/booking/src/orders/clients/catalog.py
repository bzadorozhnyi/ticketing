import uuid

from fastapi import status

from core.http import HttpClient
from orders.dto.upstream import PriceDTO


class CatalogClient:
    def __init__(self, *, http: HttpClient):
        self.http = http

    async def price(
        self, *, event_id: uuid.UUID, seat_ids: list[uuid.UUID]
    ) -> PriceDTO | None:
        """None means some seat has no price, so the order has no amount."""
        code, body = await self.http.request(
            method="POST",
            path=f"/internal/v1/events/{event_id}/price",
            expected={status.HTTP_200_OK, status.HTTP_404_NOT_FOUND},
            json={"seat_ids": [str(seat_id) for seat_id in seat_ids]},
        )
        if code == status.HTTP_404_NOT_FOUND:
            return None
        return PriceDTO.model_validate(body)
