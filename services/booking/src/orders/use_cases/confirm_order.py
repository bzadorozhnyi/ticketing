from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import InternalServerException
from orders.clients.seat_hold import SeatHoldClient
from orders.dto.messages import RefundCommand
from orders.models.order import Order
from orders.repositories.order import OrderRepository
from outbox.repositories.message import OutboxRepository


class ConfirmOrderUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        order_repository: OrderRepository,
        outbox_repository: OutboxRepository,
        seat_hold: SeatHoldClient,
        commands_queue: str,
    ):
        self.session = session
        self.order_repository = order_repository
        self.outbox_repository = outbox_repository
        self.seat_hold = seat_hold
        self.commands_queue = commands_queue

    async def execute(self, *, order: Order) -> None:
        """The money is taken, so only a definite answer ends this step. After a timeout
        the seat may be confirmed already, and a refund would give it away for free:
        the step repeats, and confirm is safe to repeat.
        """
        confirmed = await self.seat_hold.confirm(
            hold_id=order.hold_id, user_id=order.user_id
        )
        if confirmed:
            await self.order_repository.mark_confirmed(order_id=order.id)
            await self.session.commit()
            return

        if order.amount_minor is None or order.currency is None:
            raise InternalServerException(f"Order {order.id} was charged without a sum")
        command = RefundCommand(
            order_id=order.id,
            amount_minor=order.amount_minor,
            currency=order.currency,
        )
        await self.order_repository.mark_awaiting_refund(order_id=order.id)
        await self.outbox_repository.add(
            queue=self.commands_queue,
            body=command.model_dump_json(),
            traceparent=None,
        )
        await self.session.commit()
