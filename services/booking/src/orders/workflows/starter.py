import asyncio
import uuid
from contextlib import suppress

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.settings import SagaSettings
from core.temporal import TemporalClient
from orders.dto.workflow import OrderInput, SagaTimings
from orders.models.order import Order
from orders.repositories.order import OrderRepository
from orders.workflows.order_workflow import OrderWorkflow

BATCH_SIZE = 100


class WorkflowStarter:
    """Starts the workflow of each new Temporal order. The order is committed by the API
    and the workflow lives in Temporal: the two cannot be written together, so this loop
    closes the gap. Starting twice is harmless, Temporal refuses the second start."""

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        temporal: TemporalClient,
        settings: SagaSettings,
    ):
        self.session_maker = session_maker
        self.temporal = temporal
        self.settings = settings
        # Orders already handed to Temporal by this process: not asked again.
        self.started: set[uuid.UUID] = set()

    async def run(self, *, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.start_new()
            except Exception as exc:
                logger.bind(error=repr(exc)).warning("Starting workflows failed")
            with suppress(TimeoutError):
                async with asyncio.timeout(self.settings.POLL_INTERVAL_SECONDS):
                    await stop.wait()

    async def start_new(self) -> None:
        async with self.session_maker() as session:
            orders = await OrderRepository(session=session).new_temporal_orders(
                limit=BATCH_SIZE
            )
        self.started &= {order.id for order in orders}
        for order in orders:
            if order.id not in self.started:
                await self.temporal.start(
                    workflow=OrderWorkflow.run,
                    arg=self._input(order),
                    workflow_id=str(order.id),
                )
                self.started.add(order.id)

    def _input(self, order: Order) -> OrderInput:
        saga = self.settings
        return OrderInput(
            order_id=order.id,
            hold_id=order.hold_id,
            user_id=order.user_id,
            created_at=order.created_at,
            timings=SagaTimings(
                reply_wait_seconds=saga.REPLY_WAIT_SECONDS,
                max_reply_wait_seconds=saga.MAX_REPLY_WAIT_SECONDS,
                give_up_after_seconds=saga.GIVE_UP_AFTER_SECONDS,
                retry_base_delay_seconds=saga.RETRY_BASE_DELAY_SECONDS,
                max_retry_delay_seconds=saga.MAX_RETRY_DELAY_SECONDS,
            ),
        )
