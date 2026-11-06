import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import aio_pika
import pytest

from core.rabbitmq import RabbitMQClient
from core.settings import FaultSettings, RabbitMQSettings, settings
from payments.consumer import CommandConsumer
from payments.queues.commands import PaymentCommandsQueue
from payments.queues.replies import PaymentRepliesQueue
from payments.services.provider import (
    TOKEN_CRASH_AFTER_COMMIT,
    TOKEN_CRASH_BEFORE_COMMIT,
    TOKEN_DECLINED,
    TOKEN_SLOW,
)
from tests_e2e.helpers import (
    Broker,
    bodies,
    charge_command,
    count_charges,
    count_refunds_of_order,
    refund_command,
)

pytestmark = pytest.mark.anyio

SRC_PATH = Path(__file__).resolve().parent.parent
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

clients: list[RabbitMQClient] = []


@pytest.fixture
async def broker():
    connection = await aio_pika.connect_robust(settings.RABBITMQ.url)
    channel = await connection.channel(on_return_raises=True)
    broker = Broker(channel=channel, prefix=f"test-{uuid.uuid4().hex}")
    yield broker
    for client in clients:
        await client.close()
    clients.clear()
    await broker.delete()
    await connection.close()


async def declare_queues(broker, **overrides):
    defaults = {"RETRY_BASE_DELAY_SECONDS": 0.01, "QUEUE_PREFIX": broker.prefix}
    rabbit = RabbitMQSettings().model_copy(update=defaults | overrides)
    client = RabbitMQClient(settings=rabbit)
    await client.connect()
    clients.append(client)

    commands = PaymentCommandsQueue(client=client)
    replies = PaymentRepliesQueue(client=client)
    await commands.declare()
    await replies.declare()
    await broker.attach()
    return rabbit, commands, replies


async def start_consumer(broker, session_maker, **overrides):
    rabbit, commands, replies = await declare_queues(broker, **overrides)
    consumer = CommandConsumer(
        replies=replies,
        session_maker=session_maker,
        settings=rabbit,
        faults=FaultSettings(SLOW_SECONDS=1),
    )
    await commands.consume(handler=consumer.handle)


async def test_a_charge_command_gets_a_reply(broker, session_maker):
    await start_consumer(broker, session_maker)
    order_id = uuid.uuid4()

    await broker.publish(charge_command(order_id))

    reply = await broker.take(broker.replies)
    assert reply is not None
    body = json.loads(reply.body)
    assert body["type"] == "charge_result"
    assert body["order_id"] == str(order_id)
    assert body["status"] == "succeeded"
    assert reply.correlation_id == str(order_id)
    assert await count_charges(session_maker, order_id) == 1


async def test_a_declined_charge_is_a_normal_reply(broker, session_maker):
    await start_consumer(broker, session_maker)

    await broker.publish(charge_command(uuid.uuid4(), token=TOKEN_DECLINED))

    reply = await broker.take(broker.replies)
    assert reply is not None
    assert json.loads(reply.body)["status"] == "declined"


async def test_a_duplicate_command_takes_money_once(broker, session_maker):
    await start_consumer(broker, session_maker)
    order_id = uuid.uuid4()

    for _ in range(3):
        await broker.publish(charge_command(order_id))

    replies = bodies(await broker.take_all(broker.replies))
    assert len(replies) == 3
    assert len({reply["charge_id"] for reply in replies}) == 1
    assert await count_charges(session_maker, order_id) == 1


async def test_a_refund_command_returns_money(broker, session_maker):
    await start_consumer(broker, session_maker)
    order_id = uuid.uuid4()
    await broker.publish(charge_command(order_id))
    await broker.take(broker.replies)

    await broker.publish(refund_command(order_id))

    reply = await broker.take(broker.replies)
    assert reply is not None
    assert json.loads(reply.body)["status"] == "refunded"
    assert await count_refunds_of_order(session_maker, order_id) == 1


async def test_the_reply_keeps_the_trace(broker, session_maker):
    await start_consumer(broker, session_maker)

    await broker.publish(charge_command(uuid.uuid4()), {"traceparent": TRACEPARENT})

    reply = await broker.take(broker.replies)
    assert reply is not None
    assert reply.headers["traceparent"] == TRACEPARENT


@pytest.mark.parametrize(
    "body", [b"not json", b'{"type": "unknown"}'], ids=["json", "type"]
)
async def test_a_malformed_command_goes_to_the_dead_queue(broker, session_maker, body):
    await start_consumer(broker, session_maker)

    await broker.publish(body)

    dead = await broker.take(broker.dead)
    assert dead is not None
    assert dead.body == body
    assert await broker.take(broker.replies, timeout=0.1) is None


async def test_other_terms_go_straight_to_the_dead_queue(broker, session_maker):
    await start_consumer(broker, session_maker)
    order_id = uuid.uuid4()
    await broker.publish(charge_command(order_id))
    await broker.take(broker.replies)

    await broker.publish(charge_command(order_id, amount=1))

    dead = await broker.take(broker.dead)
    assert dead is not None
    assert json.loads(dead.body)["amount_minor"] == 1
    assert dead.headers["x-acquired-count"] == 1


async def test_a_handler_timeout_retries_and_succeeds(broker, session_maker):
    await start_consumer(broker, session_maker, HANDLER_TIMEOUT_SECONDS=0.1)
    order_id = uuid.uuid4()

    await broker.publish(charge_command(order_id, token=TOKEN_SLOW))

    reply = await broker.take(broker.replies, timeout=0.8)
    assert reply is not None
    assert json.loads(reply.body)["status"] == "succeeded"
    assert await count_charges(session_maker, order_id) == 1


async def test_a_command_that_keeps_failing_ends_in_the_dead_queue(broker):
    def broken_session_maker():
        raise ConnectionError("database is down")

    await start_consumer(broker, broken_session_maker, DELIVERY_LIMIT=3)

    await broker.publish(charge_command(uuid.uuid4()))

    dead = await broker.take(broker.dead, timeout=10)
    assert dead is not None
    assert dead.headers["x-delivery-count"] == 4


async def test_retries_wait_longer_each_time(broker):
    def broken_session_maker():
        raise ConnectionError("database is down")

    await start_consumer(
        broker,
        broken_session_maker,
        DELIVERY_LIMIT=3,
        RETRY_BASE_DELAY_SECONDS=0.1,
    )

    started = time.perf_counter()
    await broker.publish(charge_command(uuid.uuid4()))
    dead = await broker.take(broker.dead, timeout=10)

    assert dead is not None
    assert time.perf_counter() - started >= 0.1 + 0.2 + 0.4


def start_process(prefix: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "scripts.consume"],
        cwd=SRC_PATH,
        env=os.environ
        | {"PYTHONPATH": str(SRC_PATH), "RABBITMQ__QUEUE_PREFIX": prefix},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.parametrize(
    "token",
    [TOKEN_CRASH_BEFORE_COMMIT, TOKEN_CRASH_AFTER_COMMIT],
    ids=["before_commit", "after_commit"],
)
async def test_a_killed_consumer_redelivers_and_charges_once(
    broker, session_maker, token
):
    await declare_queues(broker)
    order_id = uuid.uuid4()
    await broker.publish(charge_command(order_id, token=token))

    first = start_process(broker.prefix)
    assert await_exit(first) == 1

    second = start_process(broker.prefix)
    try:
        reply = await broker.take(broker.replies, timeout=10)
        assert reply is not None
        assert json.loads(reply.body)["status"] == "succeeded"
        assert await count_charges(session_maker, order_id) == 1
    finally:
        second.send_signal(signal.SIGTERM)
    assert await_exit(second) == 0
    assert await broker.take(broker.commands, timeout=0.1) is None


def await_exit(process: subprocess.Popen) -> int:
    return process.wait(timeout=15)
