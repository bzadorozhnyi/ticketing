import asyncio
import json
from datetime import timedelta

import pytest
from fastapi import status
from sqlalchemy import text, update

from core.db import async_session
from core.http import HttpClient
from core.settings import UpstreamSettings
from orders.models.order import FailureReason, Order, OrderStatus, SagaStep
from orders.repositories.order import OrderRepository
from orders.saga_runner import SagaRunner
from outbox.repositories.message import OutboxRepository
from tests_e2e.helpers import (
    COMMANDS_QUEUE,
    EVENT_ID,
    SEAT_IDS,
    TRACE_ID,
    TRACEPARENT,
    USER,
    Reply,
    breaker_off,
    create_order,
    load,
    make_due,
    make_runner,
    outbox,
    trace_id_of,
)

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("empty_tables")]


class SimulatedCrash(Exception):
    pass


async def test_step_prices_the_hold_and_stores_the_charge_command(upstreams, http):
    order_id = await create_order(token="tok_card")

    assert await make_runner(http).process_next() is True

    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_CHARGE
    assert order.status == OrderStatus.PENDING
    assert order.amount_minor == 150_000
    assert order.currency == "UAH"
    assert order.next_attempt_at > order.created_at + timedelta(seconds=25)

    [message] = await outbox()
    assert message.queue == COMMANDS_QUEUE
    assert json.loads(message.body) == {
        "type": "charge",
        "order_id": str(order_id),
        "amount_minor": 150_000,
        "currency": "UAH",
        "payment_token": "tok_card",
    }


async def test_the_charge_takes_the_token_from_its_own_table(upstreams, http):
    await create_order(token="tok_apart")

    await make_runner(http).process_next()

    [message] = await outbox()
    assert json.loads(message.body)["payment_token"] == "tok_apart"


async def test_an_order_without_a_token_row_is_not_charged(upstreams, http):
    order_id = await create_order(without_token_row=True)

    assert await make_runner(http).process_next() is True

    order = await load(order_id)
    assert order.saga_step == SagaStep.STARTED
    assert order.attempts == 1
    assert await outbox() == []


async def test_step_calls_upstreams_in_order_as_the_owner(upstreams, http):
    order_id = await create_order()

    await make_runner(http).process_next()

    hold_id = (await load(order_id)).hold_id
    assert upstreams.calls == [
        ("hold", f"/internal/v1/holds/{hold_id}", USER),
        ("price", f"/internal/v1/events/{EVENT_ID}/price", None),
        ("extend", f"/internal/v1/holds/{hold_id}/extend", USER),
    ]
    assert upstreams.price_bodies == [{"seat_ids": [str(s) for s in SEAT_IDS]}]


async def test_step_continues_the_trace_of_the_request_that_created_the_order(
    upstreams, http
):
    await create_order(traceparent=TRACEPARENT)

    await make_runner(http).process_next()

    [message] = await outbox()
    assert trace_id_of(message.traceparent) == TRACE_ID
    assert len(upstreams.traceparents) == 3
    assert {trace_id_of(sent) for sent in upstreams.traceparents} == {TRACE_ID}


async def test_nothing_due_returns_false(http):
    assert await make_runner(http).process_next() is False


async def test_finished_step_is_not_taken_again(upstreams, http):
    await create_order()
    runner = make_runner(http)

    await runner.process_next()

    assert await runner.process_next() is False
    assert len(await outbox()) == 1


@pytest.mark.parametrize(
    ("reply_name", "code", "reason", "calls"),
    [
        (
            "hold",
            status.HTTP_404_NOT_FOUND,
            FailureReason.HOLD_NOT_FOUND,
            ["hold"],
        ),
        (
            "price",
            status.HTTP_404_NOT_FOUND,
            FailureReason.PRICE_NOT_FOUND,
            ["hold", "price"],
        ),
        (
            "extend",
            status.HTTP_409_CONFLICT,
            FailureReason.HOLD_EXPIRED,
            ["hold", "price", "extend"],
        ),
    ],
)
async def test_definite_refusal_fails_the_order_without_a_charge(
    upstreams, http, finished, reply_name, code, reason, calls
):
    setattr(upstreams, reply_name, Reply(status=code))
    order_id = await create_order()

    await make_runner(http).process_next()

    order = await load(order_id)
    assert order.status == OrderStatus.FAILED
    assert order.saga_step == SagaStep.DONE
    assert order.failure_reason == reason
    assert [name for name, _, _ in upstreams.calls] == calls
    assert await outbox() == []
    assert finished == [(OrderStatus.FAILED, reason)]


@pytest.mark.parametrize(
    "reply",
    [
        Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE),
        Reply(status=status.HTTP_500_INTERNAL_SERVER_ERROR),
        Reply(delay_seconds=1),
        Reply(body="not json"),
        Reply(body='{"unexpected": true}'),
    ],
    ids=["503", "500", "timeout", "not-json", "wrong-schema"],
)
async def test_no_definite_answer_postpones_the_order(upstreams, http, finished, reply):
    upstreams.hold = reply
    order_id = await create_order()
    runner = make_runner(http)

    assert await runner.process_next() is True

    order = await load(order_id)
    assert order.status == OrderStatus.PENDING
    assert order.saga_step == SagaStep.STARTED
    assert order.attempts == 1
    assert order.next_attempt_at > order.created_at
    assert await outbox() == []
    assert await runner.process_next() is False
    assert finished == []


async def test_postponed_order_succeeds_when_the_upstream_returns(upstreams, http):
    upstreams.extend = Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE)
    order_id = await create_order()
    runner = make_runner(http)
    await runner.process_next()

    upstreams.extend = Reply()
    await make_due(order_id)
    await runner.process_next()

    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_CHARGE
    assert order.attempts == 0
    assert len(await outbox()) == 1


async def test_unreachable_upstream_postpones_the_order(http, upstreams):
    closed = HttpClient(
        base_url="http://127.0.0.1:1",
        settings=UpstreamSettings(CONNECT_TIMEOUT_SECONDS=0.5, TIMEOUT_SECONDS=1),
        breaker=breaker_off(),
    )
    await closed.connect()
    order_id = await create_order()

    await make_runner(closed).process_next()
    await closed.close()

    assert (await load(order_id)).attempts == 1


async def test_retry_pause_doubles_up_to_the_limit(http):
    runner = make_runner(http, RETRY_BASE_DELAY_SECONDS=0.5, MAX_RETRY_DELAY_SECONDS=30)

    delays = [runner._retry_delay(attempts=n) for n in (0, 1, 2, 5, 6, 5000)]

    assert delays == [0.5, 1, 2, 16, 30, 30]


async def test_broken_order_does_not_stop_the_ones_behind_it(upstreams, http):
    broken = await create_order()
    upstreams.hold = Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE)
    runner = make_runner(http)
    await runner.process_next()

    upstreams.hold = Reply()
    healthy = await create_order()
    await runner.process_next()

    assert (await load(broken)).saga_step == SagaStep.STARTED
    assert (await load(healthy)).saga_step == SagaStep.AWAITING_CHARGE


async def test_crash_before_the_commit_keeps_step_and_command_together(
    upstreams, http, monkeypatch
):
    order_id = await create_order()
    runner = make_runner(http)

    async def crash(self, **kwargs):
        raise SimulatedCrash

    with monkeypatch.context() as patch:
        patch.setattr(OutboxRepository, "add", crash)
        await runner.process_next()

    order = await load(order_id)
    assert order.saga_step == SagaStep.STARTED
    assert order.amount_minor is None
    assert await outbox() == []

    await make_due(order_id)
    await runner.process_next()

    assert (await load(order_id)).saga_step == SagaStep.AWAITING_CHARGE
    assert len(await outbox()) == 1


async def test_the_order_waiting_longest_goes_first(http):
    await create_order()
    waiting_longest = await create_order()
    async with async_session() as session:
        await session.execute(
            update(Order)
            .where(Order.id == waiting_longest)
            .values(next_attempt_at=Order.created_at - timedelta(minutes=1))
        )
        await session.commit()

    async with async_session() as session:
        await session.execute(text("SET LOCAL enable_indexscan = off"))
        await session.execute(text("SET LOCAL enable_bitmapscan = off"))
        order = await OrderRepository(session=session).lock_next_due()

    assert order is not None
    assert order.id == waiting_longest


async def test_locked_order_is_skipped_without_waiting(upstreams, http):
    first = await create_order()
    second = await create_order()

    async with async_session() as holder, async_session() as other:
        taken = await OrderRepository(session=holder).lock_next_due()
        async with asyncio.timeout(2):
            rest = await OrderRepository(session=other).lock_next_due()

    assert taken is not None and rest is not None
    assert {taken.id, rest.id} == {first, second}


async def test_two_workers_run_each_order_once(upstreams, http):
    orders = [await create_order() for _ in range(30)]

    async def drain(runner: SagaRunner) -> int:
        total = 0
        while await runner.process_next():
            total += 1
        return total

    totals = await asyncio.gather(drain(make_runner(http)), drain(make_runner(http)))

    assert all(totals), totals
    assert sum(totals) == len(orders)
    assert upstreams.called("extend") == len(orders)
    commands = [json.loads(message.body)["order_id"] for message in await outbox()]
    assert sorted(commands) == sorted(str(order_id) for order_id in orders)


async def test_run_works_through_orders_and_stops_at_once(upstreams, http):
    orders = [await create_order() for _ in range(3)]
    stop = asyncio.Event()
    runner = make_runner(http, POLL_INTERVAL_SECONDS=60)

    task = asyncio.create_task(runner.run(stop=stop))
    async with asyncio.timeout(3):
        while len(await outbox()) < len(orders):
            await asyncio.sleep(0.01)
        stop.set()
        await task


async def test_run_survives_a_failed_pass(upstreams, http, monkeypatch):
    await create_order()
    runner = make_runner(http, POLL_INTERVAL_SECONDS=0.01)
    process_next = runner.process_next
    calls = []

    async def fail_once() -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("database is down")
        return await process_next()

    monkeypatch.setattr(runner, "process_next", fail_once)
    stop = asyncio.Event()

    task = asyncio.create_task(runner.run(stop=stop))
    async with asyncio.timeout(3):
        while not await outbox():
            await asyncio.sleep(0.01)
        stop.set()
        await task
