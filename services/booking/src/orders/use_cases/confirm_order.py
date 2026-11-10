from sqlalchemy.ext.asyncio import AsyncSession

from core.faults import crash_point
from core.settings import SagaSettings
from orders.clients.seat_hold import SeatHoldClient
from orders.dto.order import FinishedOrder
from orders.models.order import FailureReason, Order, OrderStatus
from orders.repositories.order import OrderRepository
from orders.services.payment_commands import PaymentCommands


class ConfirmOrderUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        order_repository: OrderRepository,
        payment_commands: PaymentCommands,
        seat_hold: SeatHoldClient,
        settings: SagaSettings,
    ):
        self.session = session
        self.order_repository = order_repository
        self.payment_commands = payment_commands
        self.seat_hold = seat_hold
        self.settings = settings

    async def execute(self, *, order: Order) -> FinishedOrder | None:
        """The money is taken, so only a definite answer ends this step. After a timeout
        the seat may be confirmed already, and a refund would give it away for free:
        the step repeats, and confirm is safe to repeat.
        """
        confirmed = await self.seat_hold.confirm(
            hold_id=order.hold_id, user_id=order.user_id
        )
        if confirmed:
            crash_point("after_confirm")
            await self.order_repository.mark_confirmed(order_id=order.id)
            await self.session.commit()
            return FinishedOrder(
                status=OrderStatus.CONFIRMED, created_at=order.created_at
            )

        await self.order_repository.mark_awaiting_refund(
            order_id=order.id,
            reason=FailureReason.REFUNDED_HOLD_LOST,
            reply_wait_seconds=self.settings.REPLY_WAIT_SECONDS,
        )
        await self.payment_commands.refund(order=order)
        await self.session.commit()
        return None
