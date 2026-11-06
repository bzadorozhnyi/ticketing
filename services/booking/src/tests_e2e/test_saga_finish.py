import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import pytest
from fastapi import status
from loguru import logger

from core.db import async_session
from core.rabbitmq import Delivery, RabbitMQClient
from core.settings import SagaSettings, settings
from orders.dto.messages import ChargeStatus, RefundStatus
from orders.models.order import FailureReason, OrderStatus, SagaStep
from orders.payment_reply_consumer import PaymentReplyConsumer
from orders.queues.replies import PaymentRepliesQueue
from orders.repositories.order import OrderRepository
from orders.use_cases.apply_charge_result import ApplyChargeResultUseCase
from outbox.repositories.message import OutboxRepository
from tests_e2e.helpers import (
    COMMANDS_QUEUE,
    USER,
    Reply,
    create_order,
    load,
    make_due,
    make_runner,
    outbox,
)

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("empty_tables")]

CHARGED: dict[str, Any] = {
    "saga_step": SagaStep.CHARGED,
    "amount_minor": 240_000,
    "currency": "UAH",
}


class SimulatedCrash(Exception):
    pass


class RecordedDelivery(Delivery):
    """Records how a message was settled, without a broker."""

    def __init__(self, *, content: bytes):
        self.content = content
        self.settled: list[str] = []

    @property
    def body(self) -> bytes:
        return self.content

    @property
    def attempts(self) -> int:
        return 0

    async def ack(self) -> None:
        self.settled.append("ack")

    async def retry(self) -> None:
        self.settled.append("retry")

    async def dead_letter(self) -> None:
        self.settled.append("dead_letter")


def make_consumer(**overrides) -> PaymentReplyConsumer:
    defaults = {"RETRY_BASE_DELAY_SECONDS": 0.001}
    return PaymentReplyConsumer(
        session_maker=async_session,
        settings=SagaSettings().model_copy(update=defaults | overrides),
    )


def charge_result(order_id: uuid.UUID, charge_status: str) -> bytes:
    return json.dumps(
        {
            "type": "charge_result",
            "order_id": str(order_id),
            "charge_id": str(uuid.uuid4()),
            "status": charge_status,
        }
    ).encode()


def refund_result(order_id: uuid.UUID, refund_status: str) -> bytes:
    return json.dumps(
        {"type": "refund_result", "order_id": str(order_id), "status": refund_status}
    ).encode()


async def deliver(content: bytes, **overrides) -> list[str]:
    delivery = RecordedDelivery(content=content)
    await make_consumer(**overrides).handle(delivery)
    return delivery.settled


async def test_successful_charge_makes_the_order_due_for_confirm(http):
    order_id = await create_order(
        saga_step=SagaStep.AWAITING_CHARGE,
        attempts=3,
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
    )

    settled = await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))

    assert settled == ["ack"]
    order = await load(order_id)
    assert order.saga_step == SagaStep.CHARGED
    assert order.status == OrderStatus.PENDING
    assert order.attempts == 0
    assert await make_runner(http).process_next() is True


async def test_declined_card_fails_the_order_and_leaves_the_hold(upstreams):
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)

    settled = await deliver(charge_result(order_id, ChargeStatus.DECLINED))

    assert settled == ["ack"]
    order = await load(order_id)
    assert order.status == OrderStatus.FAILED
    assert order.saga_step == SagaStep.DONE
    assert order.failure_reason == FailureReason.CARD_DECLINED
    assert upstreams.calls == []
    assert await outbox() == []


async def test_cancelled_charge_changes_nothing_and_is_reported():
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)
    errors: list[str] = []
    sink = logger.add(errors.append, level="ERROR", format="{message}")

    settled = await deliver(charge_result(order_id, ChargeStatus.CANCELLED))

    logger.remove(sink)
    assert settled == ["ack"]
    assert (await load(order_id)).saga_step == SagaStep.AWAITING_CHARGE
    assert errors == ["Charge was cancelled while the saga waits for it\n"]


@pytest.mark.parametrize(
    "step",
    [
        SagaStep.STARTED,
        SagaStep.CHARGED,
        SagaStep.AWAITING_REFUND,
        SagaStep.DONE,
    ],
)
@pytest.mark.parametrize(
    "charge_status", [ChargeStatus.SUCCEEDED, ChargeStatus.DECLINED]
)
async def test_charge_result_for_another_step_is_ignored(step, charge_status):
    order_id = await create_order(saga_step=step)

    settled = await deliver(charge_result(order_id, charge_status))

    assert settled == ["ack"]
    order = await load(order_id)
    assert order.saga_step == step
    assert order.status == OrderStatus.PENDING


@pytest.mark.parametrize(
    "refund_status", [RefundStatus.REFUNDED, RefundStatus.NOTHING_TO_REFUND]
)
async def test_refund_result_ends_the_order(refund_status):
    order_id = await create_order(saga_step=SagaStep.AWAITING_REFUND)

    settled = await deliver(refund_result(order_id, refund_status))

    assert settled == ["ack"]
    order = await load(order_id)
    assert order.status == OrderStatus.FAILED
    assert order.saga_step == SagaStep.DONE
    assert order.failure_reason == FailureReason.REFUNDED_HOLD_LOST


@pytest.mark.parametrize(
    "step",
    [
        SagaStep.STARTED,
        SagaStep.AWAITING_CHARGE,
        SagaStep.CHARGED,
        SagaStep.DONE,
    ],
)
async def test_refund_result_for_another_step_is_ignored(step):
    order_id = await create_order(saga_step=step)

    settled = await deliver(refund_result(order_id, RefundStatus.REFUNDED))

    assert settled == ["ack"]
    order = await load(order_id)
    assert order.saga_step == step
    assert order.status == OrderStatus.PENDING


async def test_reply_for_an_unknown_order_is_dropped():
    unknown = uuid.uuid4()

    assert await deliver(charge_result(unknown, ChargeStatus.SUCCEEDED)) == ["ack"]
    assert await deliver(refund_result(unknown, RefundStatus.REFUNDED)) == ["ack"]


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b'{"type": "unknown"}',
        b'{"type": "charge_result", "order_id": "not-a-uuid", "status": "succeeded"}',
        charge_result(uuid.uuid4(), "paid"),
    ],
    ids=["not-json", "unknown-type", "bad-id", "unknown-status"],
)
async def test_malformed_reply_is_not_retried(content):
    assert await deliver(content) == ["dead_letter"]


async def test_failed_reply_is_retried_and_not_acknowledged(monkeypatch):
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)

    async def crash(self, *, result):
        raise SimulatedCrash

    monkeypatch.setattr(ApplyChargeResultUseCase, "execute", crash)

    settled = await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))

    assert settled == ["retry"]
    assert (await load(order_id)).saga_step == SagaStep.AWAITING_CHARGE


async def test_retry_pauses_before_the_message_goes_back(monkeypatch):
    async def crash(self, *, result):
        raise SimulatedCrash

    monkeypatch.setattr(ApplyChargeResultUseCase, "execute", crash)
    started = time.perf_counter()

    await deliver(
        charge_result(uuid.uuid4(), ChargeStatus.SUCCEEDED),
        RETRY_BASE_DELAY_SECONDS=0.1,
    )

    assert time.perf_counter() - started >= 0.1


async def test_retry_pause_has_a_limit(monkeypatch):
    async def crash(self, *, result):
        raise SimulatedCrash

    monkeypatch.setattr(ApplyChargeResultUseCase, "execute", crash)

    async with asyncio.timeout(1):
        await deliver(
            charge_result(uuid.uuid4(), ChargeStatus.SUCCEEDED),
            RETRY_BASE_DELAY_SECONDS=60,
            MAX_RETRY_DELAY_SECONDS=0.01,
        )


async def test_reply_gives_up_on_a_lock_held_too_long():
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)

    async with async_session() as holder:
        await OrderRepository(session=holder).lock(order_id=order_id)
        settled = await deliver(
            charge_result(order_id, ChargeStatus.SUCCEEDED),
            REPLY_TIMEOUT_SECONDS=0.05,
        )

    assert settled == ["retry"]


async def test_reply_waits_for_a_worker_on_the_same_order():
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)

    async with async_session() as worker:
        repository = OrderRepository(session=worker)
        await repository.lock(order_id=order_id)
        reply = asyncio.create_task(
            deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))
        )
        await asyncio.sleep(0.05)
        await repository.mark_failed(
            order_id=order_id, reason=FailureReason.HOLD_EXPIRED
        )
        await worker.commit()

    assert await reply == ["ack"]
    assert (await load(order_id)).failure_reason == FailureReason.HOLD_EXPIRED


async def test_confirm_finishes_the_order_as_its_owner(upstreams, http):
    order_id = await create_order(**CHARGED)

    assert await make_runner(http).process_next() is True

    order = await load(order_id)
    assert order.status == OrderStatus.CONFIRMED
    assert order.saga_step == SagaStep.DONE
    assert order.failure_reason is None
    assert upstreams.calls == [
        ("confirm", f"/internal/v1/holds/{order.hold_id}/confirm", USER)
    ]
    assert await outbox() == []


async def test_lost_hold_stores_a_refund_with_the_charged_sum(upstreams, http):
    upstreams.confirm = Reply(status=status.HTTP_409_CONFLICT)
    order_id = await create_order(**CHARGED, attempts=2)

    await make_runner(http).process_next()

    order = await load(order_id)
    assert order.saga_step == SagaStep.AWAITING_REFUND
    assert order.status == OrderStatus.PENDING
    assert order.attempts == 0
    [message] = await outbox()
    assert message.queue == COMMANDS_QUEUE
    assert json.loads(message.body) == {
        "type": "refund",
        "order_id": str(order_id),
        "amount_minor": 240_000,
        "currency": "UAH",
    }


@pytest.mark.parametrize(
    "reply",
    [
        Reply(status=status.HTTP_503_SERVICE_UNAVAILABLE),
        Reply(status=status.HTTP_500_INTERNAL_SERVER_ERROR),
        Reply(delay_seconds=1),
    ],
    ids=["503", "500", "timeout"],
)
async def test_no_answer_after_the_charge_never_refunds(upstreams, http, reply):
    upstreams.confirm = reply
    order_id = await create_order(**CHARGED)
    runner = make_runner(http)

    for _ in range(5):
        await runner.process_next()
        await make_due(order_id)

    order = await load(order_id)
    assert order.saga_step == SagaStep.CHARGED
    assert order.status == OrderStatus.PENDING
    assert order.attempts == 5
    assert await outbox() == []

    upstreams.confirm = Reply()
    await runner.process_next()

    assert (await load(order_id)).status == OrderStatus.CONFIRMED


async def test_crash_after_confirm_repeats_it_and_finishes(
    upstreams, http, monkeypatch
):
    order_id = await create_order(**CHARGED)
    runner = make_runner(http)

    async def crash(self, *, order_id):
        raise SimulatedCrash

    with monkeypatch.context() as patch:
        patch.setattr(OrderRepository, "mark_confirmed", crash)
        await runner.process_next()
    assert (await load(order_id)).saga_step == SagaStep.CHARGED

    await make_due(order_id)
    await runner.process_next()

    assert (await load(order_id)).status == OrderStatus.CONFIRMED
    assert upstreams.called("confirm") == 2


async def test_crash_after_a_refusal_writes_one_refund(upstreams, http, monkeypatch):
    upstreams.confirm = Reply(status=status.HTTP_409_CONFLICT)
    order_id = await create_order(**CHARGED)
    runner = make_runner(http)

    async def crash(self, **kwargs):
        raise SimulatedCrash

    with monkeypatch.context() as patch:
        patch.setattr(OutboxRepository, "add", crash)
        await runner.process_next()
    assert (await load(order_id)).saga_step == SagaStep.CHARGED
    assert await outbox() == []

    await make_due(order_id)
    await runner.process_next()

    assert (await load(order_id)).saga_step == SagaStep.AWAITING_REFUND
    assert len(await outbox()) == 1


async def test_charged_order_without_a_sum_is_postponed_not_refunded(upstreams, http):
    upstreams.confirm = Reply(status=status.HTTP_409_CONFLICT)
    order_id = await create_order(saga_step=SagaStep.CHARGED)

    await make_runner(http).process_next()

    order = await load(order_id)
    assert order.saga_step == SagaStep.CHARGED
    assert order.attempts == 1
    assert await outbox() == []


@pytest.mark.parametrize(
    "step", [SagaStep.AWAITING_CHARGE, SagaStep.AWAITING_REFUND, SagaStep.DONE]
)
async def test_waiting_and_finished_orders_are_not_taken(http, step):
    await create_order(saga_step=step)

    assert await make_runner(http).process_next() is False


async def test_whole_saga_ends_confirmed(upstreams, http):
    order_id = await create_order()
    runner = make_runner(http)

    await runner.process_next()
    await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))
    await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))
    await runner.process_next()
    await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))

    order = await load(order_id)
    assert order.status == OrderStatus.CONFIRMED
    assert order.amount_minor == 150_000
    assert [name for name, _, _ in upstreams.calls] == [
        "hold",
        "price",
        "extend",
        "confirm",
    ]


async def test_whole_saga_with_a_lost_hold_ends_failed(upstreams, http):
    upstreams.confirm = Reply(status=status.HTTP_409_CONFLICT)
    order_id = await create_order()
    runner = make_runner(http)

    await runner.process_next()
    await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED))
    await runner.process_next()
    await deliver(refund_result(order_id, RefundStatus.REFUNDED))

    order = await load(order_id)
    assert order.status == OrderStatus.FAILED
    assert order.failure_reason == FailureReason.REFUNDED_HOLD_LOST
    assert [json.loads(m.body)["type"] for m in await outbox()].count("refund") == 1


@pytest.fixture
async def replies():
    """A real queue, with a client that a test may close to simulate a crash."""
    name = f"test-{uuid.uuid4().hex}.replies"
    connection = await aio_pika.connect_robust(settings.RABBITMQ.url)
    channel = await connection.channel()
    client = RabbitMQClient(settings=settings.RABBITMQ)
    await client.connect()
    queue = PaymentRepliesQueue(client=client, name=name)
    await queue.declare()
    yield queue, channel
    await client.close()
    await channel.queue_delete(name)
    await connection.close()


async def publish(channel, queue: PaymentRepliesQueue, content: bytes) -> None:
    await channel.default_exchange.publish(
        aio_pika.Message(body=content), routing_key=queue.name
    )


async def wait_for_step(order_id: uuid.UUID, step: SagaStep) -> None:
    async with asyncio.timeout(3):
        while (await load(order_id)).saga_step != step:
            await asyncio.sleep(0.01)


async def test_consumer_reads_real_replies_past_a_malformed_one(replies):
    queue, channel = replies
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)
    subscription = await queue.consume(handler=make_consumer().handle)

    await publish(channel, queue, b"not json")
    await publish(channel, queue, charge_result(order_id, ChargeStatus.SUCCEEDED))

    await wait_for_step(order_id, SagaStep.CHARGED)
    await subscription.stop()


async def test_crash_before_the_ack_redelivers_a_harmless_reply(
    replies, monkeypatch, http
):
    queue, channel = replies
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)
    seen: list[bool] = []

    async def lost_ack(self):
        seen.append(self.redelivered)

    with monkeypatch.context() as patch:
        patch.setattr(Delivery, "ack", lost_ack)
        await queue.consume(handler=make_consumer().handle)
        await publish(channel, queue, charge_result(order_id, ChargeStatus.SUCCEEDED))
        await wait_for_step(order_id, SagaStep.CHARGED)
        await queue.client.close()

    await make_runner(http).process_next()
    assert (await load(order_id)).status == OrderStatus.CONFIRMED

    client = RabbitMQClient(settings=settings.RABBITMQ)
    await client.connect()
    second = PaymentRepliesQueue(client=client, name=queue.name)
    settled = asyncio.Event()

    async def handle(delivery: Delivery) -> None:
        seen.append(delivery.redelivered)
        await make_consumer().handle(delivery)
        settled.set()

    subscription = await second.consume(handler=handle)
    async with asyncio.timeout(3):
        await settled.wait()
    await subscription.stop()
    await client.close()

    assert seen == [False, True]
    assert (await load(order_id)).status == OrderStatus.CONFIRMED
