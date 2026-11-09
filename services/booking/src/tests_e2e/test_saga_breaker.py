import asyncio
import uuid
from datetime import timedelta

import pytest
from fastapi import status

from core.breaker import CircuitBreaker, CircuitOpenError
from core.http import HttpClient
from core.settings import BreakerSettings, UpstreamSettings
from orders.clients.seat_hold import SeatHoldClient
from orders.models.order import FailureReason, OrderStatus, SagaStep
from tests_e2e.helpers import Reply, create_order, load, make_runner

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("empty_tables")]

CALLS_TO_OPEN = 3
LONG_OPEN = 60
SHORT_OPEN = 0.2


@pytest.fixture
def breaker_settings():
    return BreakerSettings(
        WINDOW=CALLS_TO_OPEN,
        MIN_CALLS=CALLS_TO_OPEN,
        FAILURE_PERCENT=50,
        OPEN_SECONDS=LONG_OPEN,
    )


@pytest.fixture
async def guarded(upstreams, breaker_settings):
    client = HttpClient(
        base_url=upstreams.url,
        settings=UpstreamSettings(CONNECT_TIMEOUT_SECONDS=0.5, TIMEOUT_SECONDS=0.2),
        breaker=CircuitBreaker(upstream="seat-hold", settings=breaker_settings),
    )
    await client.connect()
    yield client
    await client.close()


async def run_orders(http: HttpClient, count: int) -> list[uuid.UUID]:
    orders = [await create_order() for _ in range(count)]
    runner = make_runner(http)
    for _ in orders:
        await runner.process_next()
    return orders


async def test_an_open_breaker_postpones_orders_until_it_may_probe(upstreams, guarded):
    upstreams.hold = Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE)

    orders = await run_orders(guarded, CALLS_TO_OPEN + 3)

    assert upstreams.called("hold") == CALLS_TO_OPEN
    for order_id in orders[CALLS_TO_OPEN:]:
        order = await load(order_id)
        assert order.saga_step == SagaStep.STARTED
        assert order.attempts == 0, "a refused call is not a failed attempt"
        wait = order.next_attempt_at - order.created_at
        assert (
            timedelta(seconds=LONG_OPEN - 5) < wait < timedelta(seconds=LONG_OPEN + 1)
        )


async def test_timeouts_open_the_breaker(upstreams, guarded):
    upstreams.hold = Reply(delay_seconds=1)

    await run_orders(guarded, CALLS_TO_OPEN + 2)

    assert upstreams.called("hold") == CALLS_TO_OPEN


async def test_definite_answers_do_not_open_it(upstreams, guarded):
    upstreams.hold = Reply(status=status.HTTP_404_NOT_FOUND)

    orders = await run_orders(guarded, CALLS_TO_OPEN + 3)

    assert upstreams.called("hold") == len(orders)
    for order_id in orders:
        order = await load(order_id)
        assert order.status == OrderStatus.FAILED
        assert order.failure_reason == FailureReason.HOLD_NOT_FOUND


async def test_after_the_open_time_one_order_probes_and_the_rest_follow(
    upstreams, guarded, breaker_settings
):
    breaker_settings.OPEN_SECONDS = SHORT_OPEN
    upstreams.hold = Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE)
    await run_orders(guarded, CALLS_TO_OPEN)
    upstreams.hold = Reply()
    await asyncio.sleep(SHORT_OPEN + 0.05)

    recovered = await create_order()
    await make_runner(guarded).process_next()

    assert (await load(recovered)).saga_step == SagaStep.AWAITING_CHARGE
    assert guarded.breaker.acquire() is not None, "the probe closed it"


async def test_a_cancelled_probe_lets_the_next_call_probe(
    upstreams, guarded, breaker_settings
):
    breaker_settings.OPEN_SECONDS = SHORT_OPEN
    upstreams.hold = Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE)
    await run_orders(guarded, CALLS_TO_OPEN)
    await asyncio.sleep(SHORT_OPEN + 0.05)
    upstreams.hold = Reply(delay_seconds=0.15)
    seat_hold = SeatHoldClient(http=guarded)
    calls_before = upstreams.called("hold")

    probe = asyncio.create_task(seat_hold.get_hold(hold_id=uuid.uuid4(), user_id="u"))
    await asyncio.sleep(0.05)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    upstreams.hold = Reply()
    assert await seat_hold.get_hold(hold_id=uuid.uuid4(), user_id="u") is not None
    assert upstreams.called("hold") == calls_before + 2


async def test_every_upstream_has_its_own_breaker(upstreams, guarded, http):
    upstreams.hold = Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE)
    await run_orders(guarded, CALLS_TO_OPEN)

    with pytest.raises(CircuitOpenError):
        guarded.breaker.acquire()
    code, _ = await http.request(
        method="POST",
        path="/internal/v1/events/1/price",
        expected={status.HTTP_200_OK},
        json={"seat_ids": []},
    )
    assert code == status.HTTP_200_OK
