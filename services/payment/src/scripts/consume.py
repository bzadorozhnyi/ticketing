"""The payment worker: `python -m scripts.consume`."""

import asyncio
import signal

from loguru import logger

from core.db import async_session, engine
from core.logging import configure_logging
from core.rabbitmq import RabbitMQClient
from core.settings import settings
from payments.consumer import CommandConsumer
from payments.queues.commands import PaymentCommandsQueue
from payments.queues.replies import PaymentRepliesQueue


async def main() -> None:
    configure_logging()
    client = RabbitMQClient(settings=settings.RABBITMQ)
    await client.connect()

    commands = PaymentCommandsQueue(client=client)
    replies = PaymentRepliesQueue(client=client)
    await commands.declare()
    await replies.declare()

    consumer = CommandConsumer(
        replies=replies,
        session_maker=async_session,
        settings=settings.RABBITMQ,
        faults=settings.FAULTS,
    )
    subscription = await commands.consume(handler=consumer.handle)
    logger.info("Payment consumer started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop.set)
    await stop.wait()

    await subscription.stop()
    await client.close()
    await engine.dispose()
    logger.info("Payment consumer stopped")


if __name__ == "__main__":
    asyncio.run(main())
