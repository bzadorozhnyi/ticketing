import asyncio
import uuid

import aio_pika
import pytest
from aio_pika import DeliveryMode
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue
from opentelemetry.trace import SpanKind
from sqlalchemy import delete, func, select

from core.db import async_session
from core.rabbitmq import RabbitMQClient
from core.settings import OutboxSettings, settings
from core.telemetry import continue_trace
from outbox.models.message import OutboxMessage
from outbox.relay import Relay
from outbox.repositories.message import OutboxRepository

pytestmark = pytest.mark.anyio

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class SimulatedCrash(Exception):
    pass


class HangingClient(RabbitMQClient):
    async def publish(self, **kwargs) -> None:
        await asyncio.sleep(60)


@pytest.fixture(autouse=True)
async def empty_outbox():
    async with async_session() as session:
        await session.execute(delete(OutboxMessage))
        await session.commit()


@pytest.fixture
async def queue():
    connection = await aio_pika.connect_robust(settings.RABBITMQ.url)
    channel = await connection.channel()
    queue = await channel.declare_queue(
        f"test-{uuid.uuid4().hex}",
        durable=True,
        arguments={"x-queue-type": "quorum"},
    )
    yield queue
    await queue.delete(if_unused=False, if_empty=False)
    await connection.close()


@pytest.fixture
async def client():
    client = RabbitMQClient(settings=settings.RABBITMQ)
    await client.connect()
    yield client
    await client.close()


def make_relay(client: RabbitMQClient, **overrides) -> Relay:
    return Relay(
        client=client,
        session_maker=async_session,
        settings=OutboxSettings().model_copy(update=overrides),
    )


async def add_messages(
    queue_name: str, *bodies: str, traceparent: str | None = None
) -> None:
    async with async_session() as session:
        repository = OutboxRepository(session=session)
        with continue_trace(traceparent, name="test step", kind=SpanKind.INTERNAL):
            for body in bodies:
                await repository.add(queue=queue_name, body=body)
        await session.commit()


async def outbox_bodies() -> list[str]:
    async with async_session() as session:
        result = await session.execute(
            select(OutboxMessage.body).order_by(OutboxMessage.id)
        )
        return list(result.scalars())


async def receive(queue: AbstractQueue) -> list[AbstractIncomingMessage]:
    messages = []
    while (message := await queue.get(no_ack=True, fail=False)) is not None:
        messages.append(message)
    return messages


async def receive_bodies(queue: AbstractQueue) -> list[str]:
    return [message.body.decode() for message in await receive(queue)]


async def test_message_is_written_with_the_transaction_it_belongs_to():
    async with async_session() as session:
        repository = OutboxRepository(session=session)
        await repository.add(queue="q", body="rolled back")
        await session.rollback()
    async with async_session() as session:
        repository = OutboxRepository(session=session)
        await repository.add(queue="q", body="committed")
        await session.commit()

    assert await outbox_bodies() == ["committed"]


async def test_message_is_dated_by_its_insert_not_by_its_transaction():
    """A saga step calls other services before it writes the command; that time must
    not count as waiting in the outbox."""
    async with async_session() as session:
        transaction_started = await session.scalar(select(func.now()))
        await session.execute(select(func.pg_sleep(0.3)))
        await OutboxRepository(session=session).add(queue="q", body="late")
        await session.commit()
    async with async_session() as session:
        created_at = await session.scalar(select(OutboxMessage.created_at))

    assert created_at is not None and transaction_started is not None
    assert (created_at - transaction_started).total_seconds() >= 0.3


async def test_relay_publishes_oldest_first_and_empties_the_outbox(queue, client):
    await add_messages(queue.name, "first", "second", "third")

    published = await make_relay(client).publish_batch()

    assert published == 3
    assert await receive_bodies(queue) == ["first", "second", "third"]
    assert await outbox_bodies() == []


async def test_order_comes_from_the_id_not_from_the_table_layout(queue):
    older, newer = uuid.UUID(int=1), uuid.UUID(int=2)
    async with async_session() as session:
        for message_id in (newer, older):
            session.add(OutboxMessage(id=message_id, queue=queue.name, body="body"))
            await session.flush()
        await session.commit()

    async with async_session() as session:
        messages = await OutboxRepository(session=session).lock_batch(limit=1)

    assert [message.id for message in messages] == [older]


async def test_published_message_is_persistent_and_continues_its_trace(queue, client):
    await add_messages(queue.name, "traced", traceparent=TRACEPARENT)

    await make_relay(client).publish_batch()

    [message] = await receive(queue)
    assert message.delivery_mode == DeliveryMode.PERSISTENT
    assert message.content_type == "application/json"
    sent = message.headers["traceparent"]
    assert isinstance(sent, str)
    assert sent.split("-")[1] == TRACEPARENT.split("-")[1], "the same trace"


async def test_batch_is_limited(queue, client):
    await add_messages(queue.name, "first", "second", "third")

    published = await make_relay(client, BATCH_SIZE=2).publish_batch()

    assert published == 2
    assert await receive_bodies(queue) == ["first", "second"]
    assert await outbox_bodies() == ["third"]


async def test_missing_queue_keeps_the_message(queue, client):
    await add_messages(f"{queue.name}-missing", "waits")

    published = await make_relay(client).publish_batch()

    assert published == 0
    assert await outbox_bodies() == ["waits"]


async def test_failure_keeps_the_rest_and_does_not_repeat_what_was_confirmed(
    queue, client
):
    await add_messages(queue.name, "confirmed")
    await add_messages(f"{queue.name}-missing", "fails")
    await add_messages(queue.name, "behind")
    relay = make_relay(client)

    assert await relay.publish_batch() == 1
    assert await relay.publish_batch() == 0

    assert await receive_bodies(queue) == ["confirmed"]
    assert await outbox_bodies() == ["fails", "behind"]


async def test_hanging_broker_is_given_up_on(queue):
    await add_messages(queue.name, "waits")
    relay = make_relay(
        HangingClient(settings=settings.RABBITMQ), PUBLISH_TIMEOUT_SECONDS=0.05
    )

    async with asyncio.timeout(2):
        published = await relay.publish_batch()

    assert published == 0
    assert await outbox_bodies() == ["waits"]


async def test_crash_after_publish_repeats_the_message(queue, client, monkeypatch):
    await add_messages(queue.name, "once")
    relay = make_relay(client)

    async def crash(self, *, ids):
        raise SimulatedCrash

    with monkeypatch.context() as patch:
        patch.setattr(OutboxRepository, "delete", crash)
        with pytest.raises(SimulatedCrash):
            await relay.publish_batch()
    assert await outbox_bodies() == ["once"]

    await relay.publish_batch()

    assert await receive_bodies(queue) == ["once", "once"]
    assert await outbox_bodies() == []


async def test_locked_rows_are_skipped_without_waiting(queue):
    await add_messages(queue.name, "first", "second", "third")

    async with async_session() as holder, async_session() as other:
        [taken] = await OutboxRepository(session=holder).lock_batch(limit=1)
        async with asyncio.timeout(2):
            rest = await OutboxRepository(session=other).lock_batch(limit=10)

    assert taken.body == "first"
    assert [message.body for message in rest] == ["second", "third"]


async def test_two_relays_publish_each_message_once(queue, client):
    bodies = [f"message-{n}" for n in range(200)]
    await add_messages(queue.name, *bodies)

    async def drain(relay: Relay) -> int:
        total = 0
        while published := await relay.publish_batch():
            total += published
        return total

    totals = await asyncio.gather(
        drain(make_relay(client, BATCH_SIZE=10)),
        drain(make_relay(client, BATCH_SIZE=10)),
    )

    assert all(totals), totals
    assert sum(totals) == len(bodies)
    assert sorted(await receive_bodies(queue)) == sorted(bodies)
    assert await outbox_bodies() == []


async def test_run_drains_full_batches_without_a_pause_and_stops_at_once(queue, client):
    await add_messages(queue.name, "1", "2", "3", "4", "5")
    relay = make_relay(client, BATCH_SIZE=2, POLL_INTERVAL_SECONDS=60)
    stop = asyncio.Event()

    task = asyncio.create_task(relay.run(stop=stop))
    async with asyncio.timeout(2):
        while await outbox_bodies():
            await asyncio.sleep(0.01)
        stop.set()
        await task

    assert await receive_bodies(queue) == ["1", "2", "3", "4", "5"]


async def test_run_survives_a_failed_batch(queue, client, monkeypatch):
    await add_messages(queue.name, "after the outage")
    relay = make_relay(client, POLL_INTERVAL_SECONDS=0.01)
    publish_batch = relay.publish_batch
    calls = []

    async def fail_once() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("database is down")
        return await publish_batch()

    monkeypatch.setattr(relay, "publish_batch", fail_once)
    stop = asyncio.Event()

    task = asyncio.create_task(relay.run(stop=stop))
    async with asyncio.timeout(2):
        while await outbox_bodies():
            await asyncio.sleep(0.01)
        stop.set()
        await task

    assert await receive_bodies(queue) == ["after the outage"]
