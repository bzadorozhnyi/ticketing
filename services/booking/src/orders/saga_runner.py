import asyncio
from contextlib import suppress

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.settings import SagaSettings
from orders.clients.catalog import CatalogClient
from orders.clients.seat_hold import SeatHoldClient
from orders.models.order import Order, SagaStep
from orders.repositories.order import OrderRepository
from orders.use_cases.confirm_order import ConfirmOrderUseCase
from orders.use_cases.start_saga import StartSagaUseCase
from outbox.repositories.message import OutboxRepository

MAX_BACKOFF_EXPONENT = 10


class SagaRunner:
    """Takes one due order at a time and runs its step. The order row stays locked for
    the step, so two workers never run the same order.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        seat_hold: SeatHoldClient,
        catalog: CatalogClient,
        settings: SagaSettings,
    ):
        self.session_maker = session_maker
        self.seat_hold = seat_hold
        self.catalog = catalog
        self.settings = settings

    async def run(self, *, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = await self.process_next()
            except Exception as exc:
                logger.bind(error=repr(exc)).warning("Saga pass failed")
                worked = False
            if not worked:
                with suppress(TimeoutError):
                    async with asyncio.timeout(self.settings.POLL_INTERVAL_SECONDS):
                        await stop.wait()

    async def process_next(self) -> bool:
        """Returns False when nothing is due. A failed step is postponed, so one broken
        order does not stop the orders behind it.
        """
        async with self.session_maker() as session:
            repository = OrderRepository(session=session)
            order = await repository.lock_next_due()
            if order is None:
                return False

            order_id, attempts = order.id, order.attempts
            try:
                await self._run_step(session=session, order=order)
            except Exception as exc:
                logger.bind(error=repr(exc), order_id=str(order_id)).warning(
                    "Saga step failed, will retry"
                )
                await session.rollback()
                await repository.postpone(
                    order_id=order_id,
                    attempts=attempts + 1,
                    delay_seconds=self._retry_delay(attempts=attempts),
                )
                await session.commit()
        return True

    async def _run_step(self, *, session: AsyncSession, order: Order) -> None:
        order_repository = OrderRepository(session=session)
        outbox_repository = OutboxRepository(session=session)
        if order.saga_step == SagaStep.CHARGED:
            await ConfirmOrderUseCase(
                session=session,
                order_repository=order_repository,
                outbox_repository=outbox_repository,
                seat_hold=self.seat_hold,
                commands_queue=self.settings.PAYMENT_COMMANDS_QUEUE,
            ).execute(order=order)
            return

        await StartSagaUseCase(
            session=session,
            order_repository=order_repository,
            outbox_repository=outbox_repository,
            seat_hold=self.seat_hold,
            catalog=self.catalog,
            commands_queue=self.settings.PAYMENT_COMMANDS_QUEUE,
        ).execute(order=order)

    def _retry_delay(self, *, attempts: int) -> float:
        delay = self.settings.RETRY_BASE_DELAY_SECONDS * 2 ** min(
            attempts, MAX_BACKOFF_EXPONENT
        )
        return min(delay, self.settings.MAX_RETRY_DELAY_SECONDS)
