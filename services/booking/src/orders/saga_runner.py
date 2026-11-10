import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Literal

from loguru import logger
from opentelemetry.trace import SpanKind
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.breaker import CircuitOpenError
from core.settings import SagaSettings
from core.telemetry import continue_trace, record_finished_order, saga_steps
from orders.clients.catalog import CatalogClient
from orders.clients.seat_hold import SeatHoldClient
from orders.dto.order import FinishedOrder
from orders.models.order import Order, SagaStep
from orders.repositories.order import OrderRepository
from orders.services.payment_commands import PaymentCommands
from orders.use_cases.confirm_order import ConfirmOrderUseCase
from orders.use_cases.recover_order import RecoverOrderUseCase
from orders.use_cases.start_saga import StartSagaUseCase
from outbox.repositories.message import OutboxRepository

MAX_BACKOFF_EXPONENT = 10


# What happened to a step: the `outcome` of `booking.saga.steps`.
SagaOutcome = Literal["ran", "postponed", "failed"]


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
        """Returns False when nothing is due. The step continues the trace of the
        request that created the order."""
        async with self.session_maker() as session:
            repository = OrderRepository(session=session)
            order = await repository.lock_next_due()
            if order is None:
                return False

            step = order.saga_step
            name = f"saga {step}"
            with continue_trace(order.traceparent, name=name, kind=SpanKind.INTERNAL):
                outcome = await self._attempt(
                    session=session, repository=repository, order=order
                )
            saga_steps.add(1, {"saga.step": step, "outcome": outcome})
        return True

    async def _attempt(
        self, *, session: AsyncSession, repository: OrderRepository, order: Order
    ) -> SagaOutcome:
        """A failed step is postponed, so one broken order does not stop the orders
        behind it. While a breaker is open, the order waits until the breaker lets a
        probe through, not on its own schedule.
        """
        order_id, attempts = order.id, order.attempts
        overdue = self._is_overdue(order=order)
        try:
            finished = await self._run_step(
                session=session, order=order, overdue=overdue
            )
        except CircuitOpenError as exc:
            logger.bind(order_id=str(order_id), upstream=exc.upstream).info(
                "Saga step postponed, the circuit is open"
            )
            await session.rollback()
            await repository.postpone(
                order_id=order_id, attempts=attempts, delay_seconds=exc.retry_after
            )
            await session.commit()
            return "postponed"
        except Exception as exc:
            logger.bind(error=repr(exc), order_id=str(order_id)).log(
                "ERROR" if overdue else "WARNING", "Saga step failed, will retry"
            )
            await session.rollback()
            await repository.postpone(
                order_id=order_id,
                attempts=attempts + 1,
                delay_seconds=self._retry_delay(attempts=attempts),
            )
            await session.commit()
            return "failed"
        if finished:
            record_finished_order(
                created_at=finished.created_at, status=finished.status
            )
        return "ran"

    async def _run_step(
        self, *, session: AsyncSession, order: Order, overdue: bool
    ) -> FinishedOrder | None:
        order_repository = OrderRepository(session=session)
        payment_commands = PaymentCommands(
            outbox_repository=OutboxRepository(session=session),
            queue=self.settings.PAYMENT_COMMANDS_QUEUE,
        )
        if order.saga_step == SagaStep.CHARGED:
            return await ConfirmOrderUseCase(
                session=session,
                order_repository=order_repository,
                payment_commands=payment_commands,
                seat_hold=self.seat_hold,
                settings=self.settings,
            ).execute(order=order)
        if order.saga_step == SagaStep.STARTED and not overdue:
            return await StartSagaUseCase(
                session=session,
                order_repository=order_repository,
                payment_commands=payment_commands,
                seat_hold=self.seat_hold,
                catalog=self.catalog,
                settings=self.settings,
            ).execute(order=order)
        return await RecoverOrderUseCase(
            session=session,
            order_repository=order_repository,
            payment_commands=payment_commands,
            settings=self.settings,
        ).execute(order=order, overdue=overdue)

    def _is_overdue(self, *, order: Order) -> bool:
        give_up_at = order.created_at + timedelta(
            seconds=self.settings.GIVE_UP_AFTER_SECONDS
        )
        return datetime.now(UTC) >= give_up_at

    def _retry_delay(self, *, attempts: int) -> float:
        delay = self.settings.RETRY_BASE_DELAY_SECONDS * 2 ** min(
            attempts, MAX_BACKOFF_EXPONENT
        )
        return min(delay, self.settings.MAX_RETRY_DELAY_SECONDS)
