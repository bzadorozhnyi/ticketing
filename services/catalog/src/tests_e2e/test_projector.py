import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import aio_pika
import pytest
from sqlalchemy import select

from availability.consumer import SeatEventConsumer
from availability.models.seat_availability import SeatAvailability
from availability.queues.seat_events import EXCHANGE, SeatEventsQueue
from core.db import async_session
from core.rabbitmq import RabbitMQClient
from core.settings import settings


@pytest.fixture
async def projector() -> AsyncIterator[None]:
    """The projector on a queue of the test's own, bound like the real one."""
    client = RabbitMQClient(settings=settings.RABBITMQ)
    await client.connect()
    queue = SeatEventsQueue(client=client, name=f"testing.{uuid.uuid4()}")
    await queue.declare()
    consumer = SeatEventConsumer(
        session_maker=async_session, settings=settings.RABBITMQ
    )
    subscription = await queue.consume(handler=consumer.handle)
    yield
    await subscription.stop()
    await client.channel.queue_delete(queue.name)
    await client.close()


async def publish(routing_key: str, body: bytes) -> None:
    connection = await aio_pika.connect(settings.RABBITMQ.url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.get_exchange(EXCHANGE)
        await exchange.publish(aio_pika.Message(body=body), routing_key=routing_key)


def event(kind: str, event_id: uuid.UUID, *seats: tuple[uuid.UUID, int]) -> bytes:
    expiry = {"expires_at": "2026-11-17T12:00:00Z"} if kind == "SeatsHeld" else {}
    return json.dumps(
        {
            "type": kind,
            "event_id": str(event_id),
            "hold_id": str(uuid.uuid4()),
            "seats": [
                {"seat_id": str(seat_id), "version": version, **expiry}
                for seat_id, version in seats
            ],
        }
    ).encode()


async def stored_until(
    event_id: uuid.UUID, seat_id: uuid.UUID, version: int
) -> tuple[str, int]:
    """Polls until the seat has `version`, at most 5 s."""
    async with asyncio.timeout(5):
        while True:
            async with async_session() as session:
                row = await session.scalar(
                    select(SeatAvailability).where(
                        SeatAvailability.event_id == event_id,
                        SeatAvailability.seat_id == seat_id,
                    )
                )
            if row is not None and row.version == version:
                return row.status, row.version
            await asyncio.sleep(0.05)


@pytest.mark.anyio
async def test_an_event_from_the_exchange_reaches_the_copy(projector):
    event_id, seat_id = uuid.uuid4(), uuid.uuid4()

    await publish("seats.held", event("SeatsHeld", event_id, (seat_id, 3)))

    assert await stored_until(event_id, seat_id, 3) == ("held", 3)


@pytest.mark.anyio
async def test_a_late_older_event_and_a_malformed_one_change_nothing(projector):
    event_id, seat_id = uuid.uuid4(), uuid.uuid4()

    other_seat = uuid.uuid4()

    await publish("seats.released", event("SeatsReleased", event_id, (seat_id, 9)))
    await stored_until(event_id, seat_id, 9)
    await publish("seats.held", b"not json")
    late = event("SeatsHeld", event_id, (seat_id, 5), (other_seat, 10))
    await publish("seats.held", late)

    # Both seats are written by one statement, so once the other seat is there, the
    # late version of the first one has been seen too.
    assert await stored_until(event_id, other_seat, 10) == ("held", 10)
    assert await stored_until(event_id, seat_id, 9) == ("free", 9)
