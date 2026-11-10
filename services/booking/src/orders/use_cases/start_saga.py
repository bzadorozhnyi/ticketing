from sqlalchemy.ext.asyncio import AsyncSession

from core.faults import crash_point
from core.settings import SagaSettings
from orders.clients.catalog import CatalogClient
from orders.clients.seat_hold import SeatHoldClient
from orders.dto.order import FinishedOrder
from orders.models.order import FailureReason, Order, OrderStatus
from orders.repositories.order import OrderRepository
from orders.services.payment_commands import PaymentCommands


class StartSagaUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        order_repository: OrderRepository,
        payment_commands: PaymentCommands,
        seat_hold: SeatHoldClient,
        catalog: CatalogClient,
        settings: SagaSettings,
    ):
        self.session = session
        self.order_repository = order_repository
        self.payment_commands = payment_commands
        self.seat_hold = seat_hold
        self.catalog = catalog
        self.settings = settings

    async def execute(self, *, order: Order) -> FinishedOrder | None:
        """Safe to repeat: reading and extending change nothing a second time. A crash
        before the commit leaves the step `started`; after it, the command is stored
        together with the step, so a charge is never lost or sent for a stale state.
        """
        hold = await self.seat_hold.get_hold(
            hold_id=order.hold_id, user_id=order.user_id
        )
        if hold is None:
            return await self._fail(order=order, reason=FailureReason.HOLD_NOT_FOUND)

        price = await self.catalog.price(event_id=hold.event_id, seat_ids=hold.seat_ids)
        if price is None:
            return await self._fail(order=order, reason=FailureReason.PRICE_NOT_FOUND)

        extended = await self.seat_hold.extend(
            hold_id=order.hold_id, user_id=order.user_id
        )
        if not extended:
            return await self._fail(order=order, reason=FailureReason.HOLD_EXPIRED)
        crash_point("after_extend")

        await self.order_repository.mark_awaiting_charge(
            order_id=order.id,
            amount_minor=price.amount_minor,
            currency=price.currency,
            reply_wait_seconds=self.settings.REPLY_WAIT_SECONDS,
        )
        await self.payment_commands.charge(
            order_id=order.id,
            amount_minor=price.amount_minor,
            currency=price.currency,
            payment_token=order.payment_token,
        )
        await self.session.commit()
        return None

    async def _fail(self, *, order: Order, reason: FailureReason) -> FinishedOrder:
        await self.order_repository.mark_failed(order_id=order.id, reason=reason)
        await self.session.commit()
        return FinishedOrder(status=OrderStatus.FAILED, created_at=order.created_at)
