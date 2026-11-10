from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings import SagaSettings
from orders.dto.order import FinishedOrder
from orders.models.order import FailureReason, Order, OrderStatus, SagaStep
from orders.repositories.order import OrderRepository
from orders.services.payment_commands import PaymentCommands, order_sum

MAX_BACKOFF_EXPONENT = 10


class RecoverOrderUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        order_repository: OrderRepository,
        payment_commands: PaymentCommands,
        settings: SagaSettings,
    ):
        self.session = session
        self.order_repository = order_repository
        self.payment_commands = payment_commands
        self.settings = settings

    async def execute(self, *, order: Order, overdue: bool) -> FinishedOrder | None:
        """Runs when no answer came in time. Commands are safe to send again: payment
        is idempotent by order, so a repeat also brings back a lost reply.
        """
        finished = None
        if order.saga_step == SagaStep.STARTED:
            await self.order_repository.mark_failed(
                order_id=order.id, reason=FailureReason.SAGA_TIMED_OUT
            )
            finished = FinishedOrder(
                status=OrderStatus.FAILED, created_at=order.created_at
            )
        elif order.saga_step == SagaStep.AWAITING_REFUND:
            await self._repeat_refund(order=order, overdue=overdue)
        elif overdue:
            await self._give_up(order=order)
        else:
            await self._repeat_charge(order=order)
        await self.session.commit()
        return finished

    async def _repeat_charge(self, *, order: Order) -> None:
        amount_minor, currency = order_sum(order=order)
        await self.payment_commands.charge(
            order_id=order.id,
            amount_minor=amount_minor,
            currency=currency,
            payment_token=order.payment_token,
        )
        await self._wait_again(order=order)

    async def _give_up(self, *, order: Order) -> None:
        """Whether the charge happened is unknown, and a refund covers both cases: it
        returns the money, or leaves a mark that stops a charge arriving later.
        """
        await self.order_repository.mark_awaiting_refund(
            order_id=order.id,
            reason=FailureReason.CHARGE_TIMED_OUT,
            reply_wait_seconds=self.settings.REPLY_WAIT_SECONDS,
        )
        await self.payment_commands.refund(order=order)

    async def _repeat_refund(self, *, order: Order, overdue: bool) -> None:
        """Never gives up: this is the customer's money. Only a person can end this."""
        if overdue:
            logger.bind(order_id=str(order.id)).error("Refund has no answer")
        await self.payment_commands.refund(order=order)
        await self._wait_again(order=order)

    async def _wait_again(self, *, order: Order) -> None:
        attempts = order.attempts + 1
        delay = self.settings.REPLY_WAIT_SECONDS * 2 ** min(
            attempts, MAX_BACKOFF_EXPONENT
        )
        await self.order_repository.postpone(
            order_id=order.id,
            attempts=attempts,
            delay_seconds=min(delay, self.settings.MAX_REPLY_WAIT_SECONDS),
        )
