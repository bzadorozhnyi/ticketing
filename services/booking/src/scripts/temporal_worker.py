"""The Temporal worker, an experiment (ADR 0018): `python -m scripts.temporal_worker`.
Runs `OrderWorkflow` and its activities, and starts the workflow of each new order."""

import asyncio
import signal

from loguru import logger

from core.breaker import CircuitBreaker
from core.db import async_session, engine
from core.http import HttpClient
from core.logging import configure_logging
from core.rabbitmq import RabbitMQClient
from core.settings import settings
from core.telemetry import setup as setup_telemetry
from core.temporal import TemporalClient
from orders.clients.catalog import CatalogClient
from orders.clients.seat_hold import SeatHoldClient
from orders.workflows.activities import OrderActivities
from orders.workflows.order_workflow import OrderWorkflow
from orders.workflows.starter import WorkflowStarter


async def main() -> None:
    configure_logging()
    setup_telemetry()
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
    temporal = TemporalClient(settings=settings.TEMPORAL)

    activities = OrderActivities(
        session_maker=async_session,
        seat_hold=SeatHoldClient(http=seat_hold_http),
        catalog=CatalogClient(http=catalog_http),
        rabbitmq=rabbitmq,
        settings=settings.SAGA,
    )
    worker = await temporal.worker(
        workflows=[OrderWorkflow],
        activities=[
            activities.reserve,
            activities.send_charge,
            activities.send_refund,
            activities.confirm,
            activities.finish,
        ],
    )
    starter = WorkflowStarter(
        session_maker=async_session, temporal=temporal, settings=settings.SAGA
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop.set)
    async with worker:
        logger.info("Temporal worker started")
        await starter.run(stop=stop)

    await rabbitmq.close()
    await seat_hold_http.close()
    await catalog_http.close()
    await engine.dispose()
    logger.info("Temporal worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
