from typing import Any

from core.http import HttpClient


class CatalogClient:
    def __init__(self, *, http: HttpClient):
        self.http = http

    async def list_events(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        _, body, _ = await self.http.request(
            method="GET",
            path="/api/v1/events",
            params={"limit": limit, "offset": offset},
        )
        return body["items"]

    async def get_sections(self, *, event_id: str) -> dict[str, Any] | None:
        status, body, _ = await self.http.request(
            method="GET", path=f"/api/v1/events/{event_id}/sections"
        )
        return None if status in (404, 422) else body

    async def get_seats(
        self, *, event_id: str, section_id: str
    ) -> dict[str, Any] | None:
        status, body, _ = await self.http.request(
            method="GET", path=f"/api/v1/events/{event_id}/sections/{section_id}/seats"
        )
        return None if status in (404, 422) else body
