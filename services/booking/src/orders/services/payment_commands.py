import uuid

from core.exceptions import InternalServerException
from orders.dto.messages import ChargeCommand, RefundCommand
from orders.models.order import Order
from outbox.repositories.message import OutboxRepository


def order_sum(*, order: Order) -> tuple[int, str]:
    if order.amount_minor is None or order.currency is None:
        raise InternalServerException(f"Order {order.id} has no sum")
    return order.amount_minor, order.currency


class PaymentCommands:
    """Writes commands for payment to the outbox, in the caller's transaction."""

    def __init__(self, *, outbox_repository: OutboxRepository, queue: str):
        self.outbox_repository = outbox_repository
        self.queue = queue

    async def charge(
        self,
        *,
        order_id: uuid.UUID,
        amount_minor: int,
        currency: str,
        payment_token: str,
    ) -> None:
        command = ChargeCommand(
            order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
            payment_token=payment_token,
        )
        await self.outbox_repository.add(
            queue=self.queue, body=command.model_dump_json()
        )

    async def refund(self, *, order: Order) -> None:
        amount_minor, currency = order_sum(order=order)
        command = RefundCommand(
            order_id=order.id, amount_minor=amount_minor, currency=currency
        )
        await self.outbox_repository.add(
            queue=self.queue, body=command.model_dump_json()
        )
