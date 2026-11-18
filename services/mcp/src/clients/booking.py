from typing import Any

from core.http import HttpClient

CONSISTENCY_HEADER = "X-Consistency-Token"


class BookingClient:
    def __init__(self, *, http: HttpClient):
        self.http = http

    async def create_order(
        self, *, user_id: str, hold_id: str, payment_token: str, idempotency_key: str
    ) -> tuple[int, dict[str, Any], str | None]:
        """The status, the body and the consistency token that lets a read of the
        order see it at once."""
        status, body, headers = await self.http.request(
            method="POST",
            path="/api/v1/orders",
            headers={"X-User-Id": user_id, "Idempotency-Key": idempotency_key},
            json={"hold_id": hold_id, "payment_token": payment_token},
        )
        return status, body, headers.get(CONSISTENCY_HEADER)

    async def get_order(
        self, *, user_id: str, order_id: str, consistency_token: str | None
    ) -> tuple[int, dict[str, Any]]:
        headers = {"X-User-Id": user_id}
        if consistency_token:
            headers[CONSISTENCY_HEADER] = consistency_token
        status, body, _ = await self.http.request(
            method="GET", path=f"/api/v1/orders/{order_id}", headers=headers
        )
        return status, body
