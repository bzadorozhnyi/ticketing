from typing import Any

from core.http import HttpClient


class SeatHoldClient:
    def __init__(self, *, http: HttpClient):
        self.http = http

    async def hold(
        self, *, user_id: str, event_id: str, seat_ids: list[str]
    ) -> tuple[int, dict[str, Any]]:
        status, body, _ = await self.http.request(
            method="POST",
            path="/api/v1/holds",
            headers={"X-User-Id": user_id},
            json={"event_id": event_id, "seat_ids": seat_ids},
        )
        return status, body

    async def release(self, *, user_id: str, hold_id: str) -> int:
        status, _, _ = await self.http.request(
            method="DELETE",
            path=f"/api/v1/holds/{hold_id}",
            headers={"X-User-Id": user_id},
        )
        return status
