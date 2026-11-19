from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from core.faults import crash_point
from core.rabbitmq import RabbitMQClient
from core.settings import SagaSettings
from core.telemetry import record_finished_order
from orders.clients.catalog import CatalogClient
from orders.clients.seat_hold import SeatHoldClient
from orders.dto.messages import ChargeCommand, RefundCommand
from orders.dto.workflow import Finish, OrderInput, PaymentRequest, Reservation
from orders.models.order import FailureReason, Order, OrderStatus
from orders.repositories.order import OrderRepository


class OrderActivities:
    """The side effects of `OrderWorkflow`. Temporal runs an activity at least once, so
    each is safe to repeat: seat-hold's calls are, payment is idempotent by order, and
    the writes set the same values again."""

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        seat_hold: SeatHoldClient,
        catalog: CatalogClient,
        rabbitmq: RabbitMQClient,
        settings: SagaSettings,
    ):
        self.session_maker = session_maker
        self.seat_hold = seat_hold
        self.catalog = catalog
        self.rabbitmq = rabbitmq
        self.settings = settings

    @activity.defn
    async def reserve(self, order: OrderInput) -> Reservation:
        """The checks of `StartSagaUseCase`, then the sum stored with the order."""
        hold = await self.seat_hold.get_hold(
            hold_id=order.hold_id, user_id=order.user_id
        )
        if hold is None:
            return Reservation(failure_reason=FailureReason.HOLD_NOT_FOUND)
        price = await self.catalog.price(event_id=hold.event_id, seat_ids=hold.seat_ids)
        if price is None:
            return Reservation(failure_reason=FailureReason.PRICE_NOT_FOUND)
        if not await self.seat_hold.extend(
            hold_id=order.hold_id, user_id=order.user_id
        ):
            return Reservation(failure_reason=FailureReason.HOLD_EXPIRED)
        crash_point("after_extend")
        async with self.session_maker() as session:
            await OrderRepository(session=session).mark_awaiting_charge(
                order_id=order.order_id,
                amount_minor=price.amount_minor,
                currency=price.currency,
                reply_wait_seconds=self.settings.REPLY_WAIT_SECONDS,
            )
            await session.commit()
        return Reservation(amount_minor=price.amount_minor, currency=price.currency)

    @activity.defn
    async def send_charge(self, request: PaymentRequest) -> None:
        """The token is read here, so it never enters the workflow's history."""
        async with self.session_maker() as session:
            order = await session.get_one(Order, request.order_id)
            token = await OrderRepository(session=session).payment_token_of(order=order)
        command = ChargeCommand(
            order_id=request.order_id,
            amount_minor=request.amount_minor,
            currency=request.currency,
            payment_token=token,
        )
        await self._publish(body=command.model_dump_json())
        crash_point("after_publish")

    @activity.defn
    async def send_refund(self, request: PaymentRequest) -> None:
        async with self.session_maker() as session:
            await OrderRepository(session=session).mark_awaiting_refund(
                order_id=request.order_id,
                reason=FailureReason(request.refund_reason),
                reply_wait_seconds=self.settings.REPLY_WAIT_SECONDS,
            )
            await session.commit()
        command = RefundCommand(
            order_id=request.order_id,
            amount_minor=request.amount_minor,
            currency=request.currency,
        )
        await self._publish(body=command.model_dump_json())
        crash_point("after_publish")

    @activity.defn
    async def confirm(self, order: OrderInput) -> bool:
        confirmed = await self.seat_hold.confirm(
            hold_id=order.hold_id, user_id=order.user_id
        )
        crash_point("after_confirm")
        return confirmed

    @activity.defn
    async def finish(self, finish: Finish) -> None:
        """Only a pending order is finished: a repeat changes nothing, counts once."""
        async with self.session_maker() as session:
            repository = OrderRepository(session=session)
            order = await repository.lock(order_id=finish.order_id)
            if order is None or order.status != OrderStatus.PENDING:
                await session.rollback()
                return
            if finish.outcome == "confirmed":
                await repository.mark_confirmed(order_id=order.id)
            elif finish.outcome == "refunded":
                await repository.mark_refund_finished(order_id=order.id)
            else:
                await repository.mark_failed(
                    order_id=order.id, reason=FailureReason(finish.failure_reason)
                )
            await session.commit()
        status = (
            OrderStatus.CONFIRMED
            if finish.outcome == "confirmed"
            else OrderStatus.FAILED
        )
        record_finished_order(
            created_at=order.created_at,
            status=status,
            failure_reason=order.failure_reason or finish.failure_reason,
        )

    async def _publish(self, *, body: str) -> None:
        await self.rabbitmq.publish(
            queue=self.settings.PAYMENT_COMMANDS_QUEUE, body=body.encode()
        )
