import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import status
from loguru import logger
from sqlalchemy import update

from core.db import async_session
from core.settings import SagaSettings
from orders.dto.messages import ChargeStatus, RefundStatus
from orders.models.order import FailureReason, Order, OrderStatus, SagaStep
from orders.payment_reply_consumer import PaymentReplyConsumer
from orders.repositories.order import OrderRepository
from outbox.repositories.message import OutboxRepository
from tests_e2e.helpers import (
    COMMANDS_QUEUE,
    Reply,
    create_order,
    load,
    make_due,
    make_runner,
    outbox,
    store_token,
)
from tests_e2e.test_saga_finish import (
    RecordedDelivery,
    SimulatedCrash,
    charge_result,
    refund_result,
)

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("empty_tables")]

WAITING_CHARGE: dict[str, Any] = {
    "saga_step": SagaStep.AWAITING_CHARGE,
    "amount_minor": 240_000,
    "currency": "UAH",
}
WAITING_REFUND: dict[str, Any] = {
    "saga_step": SagaStep.AWAITING_REFUND,
    "amount_minor": 240_000,
    "currency": "UAH",
    "failure_reason": FailureReason.REFUNDED_HOLD_LOST,
}


async def make_overdue(order_id: uuid.UUID) -> None:
    async with async_session() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(
                created_at=Order.created_at - timedelta(hours=1),
                next_attempt_at=Order.next_attempt_at - timedelta(hours=1),
            )
        )
        await session.commit()


async def bodies() -> list[dict]:
    return [json.loads(message.body) for message in await outbox()]


async def deliver(content: bytes) -> None:
    delivery = RecordedDelivery(content=content)
    await PaymentReplyConsumer(
        session_maker=async_session, settings=SagaSettings()
    ).handle(delivery)
    assert delivery.settled == ["ack"]


async def test_missing_reply_sends_the_same_charge_again(http):
    order_id = await create_order(token="tok_card", **WAITING_CHARGE)

    assert await make_runner(http).process_next() is True

    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_CHARGE
    assert order.status == OrderStatus.PENDING
    assert order.attempts == 1
    assert order.failure_reason is None
    [message] = await outbox()
    assert message.queue == COMMANDS_QUEUE
    assert json.loads(message.body) == {
        "type": "charge",
        "order_id": str(order_id),
        "amount_minor": 240_000,
        "currency": "UAH",
        "payment_token": "tok_card",
    }


async def test_a_repeated_charge_takes_the_token_from_its_own_table(http):
    order_id = await create_order(token="tok_in_order", **WAITING_CHARGE)
    await store_token(order_id, "tok_apart")

    await make_runner(http).process_next()

    [body] = await bodies()
    assert body["payment_token"] == "tok_apart"


async def test_repeated_charge_waits_before_the_next_one(http):
    await create_order(**WAITING_CHARGE)
    runner = make_runner(http)

    await runner.process_next()

    assert await runner.process_next() is False
    assert len(await outbox()) == 1


@pytest.mark.parametrize(
    ("attempts", "seconds"), [(0, 60), (1, 120), (2, 120), (5000, 120)]
)
async def test_wait_doubles_up_to_the_limit(http, attempts, seconds):
    order_id = await create_order(**WAITING_CHARGE, attempts=attempts)

    await make_runner(
        http, REPLY_WAIT_SECONDS=30.0, MAX_REPLY_WAIT_SECONDS=120
    ).process_next()

    order = await load(order_id)
    wait = order.next_attempt_at - order.created_at
    assert timedelta(seconds=seconds - 5) < wait < timedelta(seconds=seconds + 5)


async def test_lost_reply_is_healed_by_the_repeated_charge(upstreams, http):
    order_id = await create_order(**WAITING_CHARGE)
    runner = make_runner(http)

    await runner.process_next()
    await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))
    await runner.process_next()

    order = await load(order_id)
    assert order.status == OrderStatus.CONFIRMED
    assert [body["type"] for body in await bodies()] == ["charge"]


async def test_crash_while_repeating_changes_nothing(http, monkeypatch):
    order_id = await create_order(**WAITING_CHARGE)
    runner = make_runner(http)

    async def crash(self, **kwargs):
        raise SimulatedCrash

    with monkeypatch.context() as patch:
        patch.setattr(OrderRepository, "postpone", crash)
        with pytest.raises(SimulatedCrash):
            await runner.process_next()

    assert (await load(order_id)).attempts == 0
    assert await outbox() == []


async def test_started_order_past_the_deadline_fails_without_any_call(
    upstreams, http, finished
):
    order_id = await create_order()
    await make_overdue(order_id)

    assert await make_runner(http).process_next() is True

    order = await load(order_id)
    assert order.status == OrderStatus.FAILED
    assert order.saga_step == SagaStep.DONE
    assert order.failure_reason == FailureReason.SAGA_TIMED_OUT
    assert upstreams.calls == []
    assert await outbox() == []
    assert finished == [(OrderStatus.FAILED, FailureReason.SAGA_TIMED_OUT)]


async def test_order_just_inside_the_deadline_still_runs(upstreams, http):
    order_id = await create_order()

    await make_runner(http, GIVE_UP_AFTER_SECONDS=3600).process_next()

    assert (await load(order_id)).saga_step == SagaStep.AWAITING_CHARGE


async def test_giving_up_sends_a_refund_and_waits_for_it(http):
    order_id = await create_order(**WAITING_CHARGE, attempts=4)
    await make_overdue(order_id)

    await make_runner(http).process_next()

    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_REFUND
    assert order.status == OrderStatus.PENDING
    assert order.failure_reason == FailureReason.CHARGE_TIMED_OUT
    assert order.attempts == 0
    assert await bodies() == [
        {
            "type": "refund",
            "order_id": str(order_id),
            "amount_minor": 240_000,
            "currency": "UAH",
        }
    ]
    assert await make_runner(http).process_next() is False


async def test_crash_while_giving_up_repeats_it_with_one_refund(http, monkeypatch):
    order_id = await create_order(**WAITING_CHARGE)
    await make_overdue(order_id)
    runner = make_runner(http)

    async def crash(self, **kwargs):
        raise SimulatedCrash

    with monkeypatch.context() as patch:
        patch.setattr(OutboxRepository, "add", crash)
        await runner.process_next()

    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_CHARGE
    assert order.failure_reason is None
    assert await outbox() == []

    await make_due(order_id)
    await runner.process_next()

    assert (await load(order_id)).saga_step == SagaStep.AWAITING_REFUND
    assert [body["type"] for body in await bodies()] == ["refund"]


@pytest.mark.parametrize(
    "refund_status", [RefundStatus.REFUNDED, RefundStatus.NOTHING_TO_REFUND]
)
async def test_answered_refund_ends_a_timed_out_order(http, finished, refund_status):
    order_id = await create_order(**WAITING_CHARGE)
    await make_overdue(order_id)
    await make_runner(http).process_next()

    await deliver(refund_result(order_id, refund_status))

    order = await load(order_id)
    assert order.status == OrderStatus.FAILED
    assert order.saga_step == SagaStep.DONE
    assert order.failure_reason == FailureReason.CHARGE_TIMED_OUT
    assert finished == [(OrderStatus.FAILED, FailureReason.CHARGE_TIMED_OUT)]


async def test_late_charge_result_after_giving_up_is_ignored(http):
    order_id = await create_order(**WAITING_CHARGE)
    await make_overdue(order_id)
    await make_runner(http).process_next()

    await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))

    assert (await load(order_id)).saga_step == SagaStep.AWAITING_REFUND


async def test_missing_refund_answer_sends_the_refund_again(http):
    order_id = await create_order(**WAITING_REFUND)

    await make_runner(http).process_next()

    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_REFUND
    assert order.attempts == 1
    assert order.failure_reason == FailureReason.REFUNDED_HOLD_LOST
    assert [body["type"] for body in await bodies()] == ["refund"]


async def test_refund_is_never_given_up_and_is_reported(http):
    order_id = await create_order(**WAITING_REFUND)
    await make_overdue(order_id)
    errors: list[str] = []
    sink = logger.add(errors.append, level="ERROR", format="{message}")

    await make_runner(http).process_next()

    logger.remove(sink)
    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_REFUND
    assert order.status == OrderStatus.PENDING
    assert [body["type"] for body in await bodies()] == ["refund"]
    assert errors == ["Refund has no answer\n"]


async def test_refund_inside_the_deadline_is_not_reported(http):
    await create_order(**WAITING_REFUND)
    errors: list[str] = []
    sink = logger.add(errors.append, level="ERROR", format="{message}")

    await make_runner(http).process_next()

    logger.remove(sink)
    assert errors == []


async def test_charged_order_past_the_deadline_keeps_trying_and_is_reported(
    upstreams, http
):
    upstreams.confirm = Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE)
    order_id = await create_order(
        saga_step=SagaStep.CHARGED, amount_minor=240_000, currency="UAH"
    )
    await make_overdue(order_id)
    errors: list[str] = []
    sink = logger.add(errors.append, level="ERROR", format="{message}")

    await make_runner(http).process_next()

    logger.remove(sink)
    order = await load(order_id)
    assert order.saga_step == SagaStep.CHARGED
    assert await outbox() == []
    assert errors == ["Saga step failed, will retry\n"]


async def test_charged_order_past_the_deadline_still_confirms(upstreams, http):
    order_id = await create_order(
        saga_step=SagaStep.CHARGED, amount_minor=240_000, currency="UAH"
    )
    await make_overdue(order_id)

    await make_runner(http).process_next()

    assert (await load(order_id)).status == OrderStatus.CONFIRMED


async def test_failed_step_inside_the_deadline_is_only_a_warning(upstreams, http):
    upstreams.confirm = Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE)
    await create_order(saga_step=SagaStep.CHARGED)
    errors: list[str] = []
    sink = logger.add(errors.append, level="ERROR", format="{message}")

    await make_runner(http).process_next()

    logger.remove(sink)
    assert errors == []


async def test_waiting_order_without_a_sum_is_postponed(http):
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)

    await make_runner(http).process_next()

    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_CHARGE
    assert order.attempts == 1
    assert await outbox() == []
