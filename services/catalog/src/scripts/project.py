"""The projector: `python -m scripts.project`. Keeps `seat_availability` from
seat-hold's events."""

import asyncio
import signal

from loguru import logger

from availability.consumer import SeatEventConsumer
from availability.queues.seat_events import SeatEventsQueue
from core.db import async_session, engine
from core.logging import configure_logging
from core.rabbitmq import RabbitMQClient
from core.settings import settings
from core.telemetry import setup as setup_telemetry


async def main() -> None:
    configure_logging()
    setup_telemetry()
    client = RabbitMQClient(settings=settings.RABBITMQ)
    await client.connect()

    queue = SeatEventsQueue(client=client)
    await queue.declare()
    consumer = SeatEventConsumer(
        session_maker=async_session, settings=settings.RABBITMQ
    )
    subscription = await queue.consume(handler=consumer.handle)
    logger.info("Projector started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop.set)
    await stop.wait()

    await subscription.stop()
    await client.close()
    await engine.dispose()
    logger.info("Projector stopped")


if __name__ == "__main__":
    asyncio.run(main())
