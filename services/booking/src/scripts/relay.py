"""The outbox relay: `python -m scripts.relay`."""

import asyncio
import signal

from loguru import logger

from core.db import async_session, engine
from core.logging import configure_logging
from core.rabbitmq import RabbitMQClient
from core.settings import settings
from core.telemetry import setup as setup_telemetry
from outbox.relay import Relay


async def main() -> None:
    configure_logging()
    setup_telemetry()
    client = RabbitMQClient(settings=settings.RABBITMQ)
    await client.connect()

    relay = Relay(client=client, session_maker=async_session, settings=settings.OUTBOX)
    logger.info("Outbox relay started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop.set)
    await relay.run(stop=stop)

    await client.close()
    await engine.dispose()
    logger.info("Outbox relay stopped")


if __name__ == "__main__":
    asyncio.run(main())
