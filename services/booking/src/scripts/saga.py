"""The saga worker: `python -m scripts.saga`."""

import asyncio
import signal

from loguru import logger

from core.breaker import CircuitBreaker
from core.db import async_session, engine
from core.http import HttpClient
from core.logging import configure_logging
from core.rabbitmq import RabbitMQClient
from core.settings import settings
from orders.clients.catalog import CatalogClient
from orders.clients.seat_hold import SeatHoldClient
from orders.payment_reply_consumer import PaymentReplyConsumer
from orders.queues.replies import PaymentRepliesQueue
from orders.saga_runner import SagaRunner


async def main() -> None:
    configure_logging()
    seat_hold_http = HttpClient(
        base_url=settings.UPSTREAMS.SEAT_HOLD_URL,
        settings=settings.UPSTREAMS,
        breaker=CircuitBreaker(upstream="seat-hold", settings=settings.BREAKER),
    )
    catalog_http = HttpClient(
        base_url=settings.UPSTREAMS.CATALOG_URL,
        settings=settings.UPSTREAMS,
        breaker=CircuitBreaker(upstream="catalog", settings=settings.BREAKER),
    )
    await seat_hold_http.connect()
    await catalog_http.connect()
    rabbitmq = RabbitMQClient(settings=settings.RABBITMQ)
    await rabbitmq.connect()

    replies = PaymentRepliesQueue(
        client=rabbitmq, name=settings.SAGA.PAYMENT_REPLIES_QUEUE
    )
    await replies.declare()
    consumer = PaymentReplyConsumer(session_maker=async_session, settings=settings.SAGA)
    subscription = await replies.consume(handler=consumer.handle)

    runner = SagaRunner(
        session_maker=async_session,
        seat_hold=SeatHoldClient(http=seat_hold_http),
        catalog=CatalogClient(http=catalog_http),
        settings=settings.SAGA,
    )
    logger.info("Saga worker started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop.set)
    await runner.run(stop=stop)

    await subscription.stop()
    await rabbitmq.close()
    await seat_hold_http.close()
    await catalog_http.close()
    await engine.dispose()
    logger.info("Saga worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
