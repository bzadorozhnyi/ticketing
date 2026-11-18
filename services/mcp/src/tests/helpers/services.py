from typing import Any

from aiohttp import web

EVENT = "01a1e3b8-0276-7f08-ac27-9107afe6caae"
SECTION = "01a1cef1-e8b4-7b13-8a46-f9cea70bfe3e"
TAKEN = "seat-taken"


class FakeServices:
    """catalog, seat-hold and booking on one port: their paths do not overlap. Records
    every request that changes something.
    """

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/v1/events", self.events)
        app.router.add_get("/api/v1/events/{event_id}/sections", self.sections)
        app.router.add_get(
            "/api/v1/events/{event_id}/sections/{section_id}/seats", self.seats
        )
        app.router.add_post("/api/v1/holds", self.hold)
        app.router.add_delete("/api/v1/holds/{hold_id}", self.release)
        app.router.add_post("/api/v1/orders", self.order)
        app.router.add_get("/api/v1/orders/{order_id}", self.get_order)
        return app

    async def _record(self, request: web.Request) -> dict[str, Any]:
        body = await request.json() if request.can_read_body else None
        call = {
            "method": request.method,
            "path": request.path,
            "headers": request.headers,
            "body": body,
        }
        self.calls.append(call)
        return call

    async def events(self, request: web.Request) -> web.Response:
        venue = {"id": "v1", "name": "Palace of Ukraine", "city": "Kyiv"}
        items = [
            {
                "id": EVENT,
                "title": "Rock Night",
                "starts_at": "2026-12-01T19:00:00Z",
                "sales_start_at": "2026-11-01T10:00:00Z",
                "currency": "UAH",
                "venue": venue,
            },
            {
                "id": "other",
                "title": "Jazz Evening",
                "starts_at": "2026-12-02T19:00:00Z",
                "sales_start_at": "2026-11-01T10:00:00Z",
                "currency": "UAH",
                "venue": {"id": "v2", "name": "Lviv Opera", "city": "Lviv"},
            },
        ]
        return web.json_response({"items": items, "limit": 100, "offset": 0})

    async def sections(self, request: web.Request) -> web.Response:
        if request.match_info["event_id"] != EVENT:
            return web.json_response(
                {"code": "not_found", "message": "no event"}, status=404
            )
        sections = [
            {"id": SECTION, "name": "Parter", "seat_count": 2, "price_minor": 150000}
        ]
        return web.json_response(
            {
                "event_id": EVENT,
                "venue_id": "v1",
                "currency": "UAH",
                "sections": sections,
            }
        )

    async def seats(self, request: web.Request) -> web.Response:
        seats = [
            {"id": "seat-1", "row": 1, "number": 1},
            {"id": TAKEN, "row": 1, "number": 2},
        ]
        return web.json_response(
            {"event_id": EVENT, "section_id": SECTION, "seats": seats}
        )

    async def hold(self, request: web.Request) -> web.Response:
        call = await self._record(request)
        seat_ids = call["body"]["seat_ids"]
        if TAKEN in seat_ids:
            body = {
                "code": "conflict",
                "message": "Some seats are taken",
                "details": [TAKEN],
            }
            return web.json_response(body, status=409)
        body = {
            "hold_id": "hold-1",
            "event_id": call["body"]["event_id"],
            "seat_ids": seat_ids,
            "expires_at": "2026-11-18T12:05:00Z",
        }
        return web.json_response(body, status=201)

    async def release(self, request: web.Request) -> web.Response:
        await self._record(request)
        return web.Response(status=204)

    async def order(self, request: web.Request) -> web.Response:
        call = await self._record(request)
        body = {
            "id": "order-1",
            "hold_id": call["body"]["hold_id"],
            "status": "pending",
            "failure_reason": None,
            "created_at": "2026-11-18T12:00:00Z",
        }
        return web.json_response(
            body, status=202, headers={"X-Consistency-Token": "0/1A2B3C"}
        )

    async def get_order(self, request: web.Request) -> web.Response:
        await self._record(request)
        body = {
            "id": request.match_info["order_id"],
            "hold_id": "hold-1",
            "status": "confirmed",
            "failure_reason": None,
            "created_at": "2026-11-18T12:00:00Z",
        }
        return web.json_response(body)
