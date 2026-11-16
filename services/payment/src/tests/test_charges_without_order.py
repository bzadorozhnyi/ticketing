import socket
import uuid
from datetime import timedelta

import pytest
from aiohttp import web
from sqlalchemy import func, update

from core.settings import CoherenceSettings
from payments.clients.booking import BookingClient, BookingUnavailableError
from payments.models.charge import Charge, ChargeStatus
from payments.repositories.charge import ChargeRepository
from payments.repositories.refund import RefundRepository
from payments.use_cases.find_charges_without_order import FindChargesWithoutOrderUseCase

pytestmark = pytest.mark.anyio


class Booking:
    """A real HTTP server in the test's place of booking: it knows `orders` and counts
    the requests; `status` other than 200 makes it fail."""

    def __init__(self, *, url: str):
        self.url = url
        self.orders: set[str] = set()
        self.requests = 0
        self.status = 200

    async def existing(self, request: web.Request) -> web.Response:
        self.requests += 1
        if self.status != 200:
            return web.json_response(
                {"code": "service_unavailable"}, status=self.status
            )
        asked = (await request.json())["order_ids"]
        return web.json_response({"order_ids": [o for o in asked if o in self.orders]})


@pytest.fixture
async def booking():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    stub = Booking(url=f"http://127.0.0.1:{sock.getsockname()[1]}")
    app = web.Application()
    app.router.add_post("/internal/v1/orders/existing", stub.existing)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.SockSite(runner, sock).start()
    yield stub
    await runner.cleanup()


async def charge(db_session, charge_factory, *, age: timedelta, **kwargs) -> Charge:
    made = await charge_factory(**kwargs)
    await db_session.execute(
        update(Charge).where(Charge.id == made.id).values(created_at=func.now() - age)
    )
    return made


async def find(db_session, url: str, **settings) -> list[uuid.UUID]:
    return await FindChargesWithoutOrderUseCase(
        session=db_session,
        charge_repository=ChargeRepository(session=db_session),
        booking=BookingClient(base_url=url, timeout_seconds=2),
        settings=CoherenceSettings(BOOKING_URL=url, **settings),
    ).execute()


async def test_a_charge_without_its_order_is_reported(
    db_session, charge_factory, booking
):
    lost = await charge(db_session, charge_factory, age=timedelta(minutes=5))
    kept = await charge(db_session, charge_factory, age=timedelta(minutes=5))
    booking.orders = {str(kept.order_id)}

    assert await find(db_session, booking.url) == [lost.order_id]


@pytest.mark.parametrize(
    ("age", "status"),
    [
        (timedelta(seconds=10), ChargeStatus.SUCCEEDED),  # still in its grace period
        (timedelta(days=2), ChargeStatus.SUCCEEDED),  # older than the lookback
        (timedelta(minutes=5), ChargeStatus.DECLINED),  # took no money
        (timedelta(minutes=5), ChargeStatus.PENDING),  # not finished yet
    ],
    ids=["young", "old", "declined", "pending"],
)
async def test_charges_outside_the_check_are_not_asked_about(
    db_session, charge_factory, booking, age, status
):
    await charge(db_session, charge_factory, age=age, status=status)

    assert await find(db_session, booking.url) == []
    assert booking.requests == 0


async def test_a_refunded_charge_is_not_reported(db_session, charge_factory, booking):
    refunded = await charge(db_session, charge_factory, age=timedelta(minutes=5))
    await RefundRepository(session=db_session).insert_if_absent(charge_id=refunded.id)

    assert await find(db_session, booking.url) == []


async def test_orders_are_asked_about_in_batches(db_session, charge_factory, booking):
    for _ in range(3):
        await charge(db_session, charge_factory, age=timedelta(minutes=5))

    missing = await find(db_session, booking.url, BATCH_SIZE=2)

    assert len(missing) == 3
    assert booking.requests == 2


async def test_booking_failing_is_not_a_clean_result(
    db_session, charge_factory, booking
):
    await charge(db_session, charge_factory, age=timedelta(minutes=5))
    booking.status = 503

    with pytest.raises(BookingUnavailableError):
        await find(db_session, booking.url)


async def test_booking_unreachable_is_not_a_clean_result(db_session, charge_factory):
    await charge(db_session, charge_factory, age=timedelta(minutes=5))

    with pytest.raises(BookingUnavailableError):
        await find(db_session, "http://127.0.0.1:1")
