import asyncio
import uuid
from dataclasses import dataclass, field

from aiohttp import web
from sqlalchemy import select, update

from core.breaker import CircuitBreaker
from core.db import async_session
from core.http import HttpClient
from core.settings import BreakerSettings, SagaSettings
from orders.clients.catalog import CatalogClient
from orders.clients.seat_hold import SeatHoldClient
from orders.models.order import Order, OrderStatus
from orders.saga_runner import SagaRunner
from outbox.models.message import OutboxMessage

USER = "user-1"
COMMANDS_QUEUE = "commands-under-test"
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
    confirm: Reply = field(default_factory=Reply)
    amount_minor: int = 150_000
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)
    price_bodies: list[dict] = field(default_factory=list)
    traceparents: list[str | None] = field(default_factory=list)

    def called(self, name: str) -> int:
        return sum(1 for call_name, _, _ in self.calls if call_name == name)

    async def answer(
        self, *, name: str, reply: Reply, request: web.Request, body: dict
    ) -> web.Response:
        self.calls.append((name, request.path, request.headers.get("X-User-Id")))
        self.traceparents.append(request.headers.get("traceparent"))
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

    async def confirm_hold(self, request: web.Request) -> web.Response:
        return await self.answer(
            name="confirm",
            reply=self.confirm,
            request=request,
            body={"hold_id": request.match_info["hold_id"]},
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
        app.router.add_post("/internal/v1/holds/{hold_id}/confirm", self.confirm_hold)
        app.router.add_post("/internal/v1/events/{event_id}/price", self.price_seats)
        return app


TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"


def trace_id_of(traceparent: str | None) -> str | None:
    return traceparent.split("-")[1] if traceparent else None


def make_runner(http: HttpClient, **overrides) -> SagaRunner:
    defaults = {
        "PAYMENT_COMMANDS_QUEUE": COMMANDS_QUEUE,
        "RETRY_BASE_DELAY_SECONDS": 60,
    }
    return SagaRunner(
        session_maker=async_session,
        seat_hold=SeatHoldClient(http=http),
        catalog=CatalogClient(http=http),
        settings=SagaSettings().model_copy(update=defaults | overrides),
    )


async def create_order(*, token: str = "tok_ok", **fields) -> uuid.UUID:
    async with async_session() as session:
        order = Order(
            user_id=USER,
            idempotency_key=str(uuid.uuid4()),
            hold_id=uuid.uuid4(),
            payment_token=token,
            status=OrderStatus.PENDING,
            **fields,
        )
        session.add(order)
        await session.commit()
        return order.id


async def load(order_id: uuid.UUID) -> Order:
    async with async_session() as session:
        order = await session.get(Order, order_id)
        assert order is not None
        return order


async def outbox() -> list[OutboxMessage]:
    async with async_session() as session:
        result = await session.execute(select(OutboxMessage))
        return list(result.scalars())


async def make_due(order_id: uuid.UUID) -> None:
    async with async_session() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(next_attempt_at=Order.created_at)
        )
        await session.commit()


def breaker_off() -> CircuitBreaker:
    """More calls are needed than the window holds, so it never opens."""
    return CircuitBreaker(
        upstream="test", settings=BreakerSettings(WINDOW=10, MIN_CALLS=1000)
    )
