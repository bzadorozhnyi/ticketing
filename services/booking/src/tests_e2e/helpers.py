import asyncio
import uuid
from dataclasses import dataclass, field

from aiohttp import web

EVENT_ID = uuid.uuid4()
SEAT_IDS = [uuid.uuid4(), uuid.uuid4()]


@dataclass
class Reply:
    status: int = 200
    delay_seconds: float = 0
    body: str | None = None


@dataclass
class Upstreams:
    """A real HTTP server standing in for seat-hold and catalog."""

    url: str = ""
    hold: Reply = field(default_factory=Reply)
    price: Reply = field(default_factory=Reply)
    extend: Reply = field(default_factory=Reply)
    amount_minor: int = 150_000
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)
    price_bodies: list[dict] = field(default_factory=list)

    def called(self, name: str) -> int:
        return sum(1 for call_name, _, _ in self.calls if call_name == name)

    async def answer(
        self, *, name: str, reply: Reply, request: web.Request, body: dict
    ) -> web.Response:
        self.calls.append((name, request.path, request.headers.get("X-User-Id")))
        await asyncio.sleep(reply.delay_seconds)
        if reply.body is not None:
            return web.Response(
                status=reply.status, text=reply.body, content_type="application/json"
            )
        return web.json_response(body, status=reply.status)

    async def get_hold(self, request: web.Request) -> web.Response:
        return await self.answer(
            name="hold",
            reply=self.hold,
            request=request,
            body={
                "hold_id": request.match_info["hold_id"],
                "event_id": str(EVENT_ID),
                "seat_ids": [str(seat_id) for seat_id in SEAT_IDS],
                "expires_at": "2026-11-06T12:00:00Z",
            },
        )

    async def extend_hold(self, request: web.Request) -> web.Response:
        return await self.answer(
            name="extend",
            reply=self.extend,
            request=request,
            body={
                "hold_id": request.match_info["hold_id"],
                "expires_at": "2026-11-06T12:15:00Z",
            },
        )

    async def price_seats(self, request: web.Request) -> web.Response:
        self.price_bodies.append(await request.json())
        return await self.answer(
            name="price",
            reply=self.price,
            request=request,
            body={"amount_minor": self.amount_minor, "currency": "UAH"},
        )

    def application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/internal/v1/holds/{hold_id}", self.get_hold)
        app.router.add_post("/internal/v1/holds/{hold_id}/extend", self.extend_hold)
        app.router.add_post("/internal/v1/events/{event_id}/price", self.price_seats)
        return app
