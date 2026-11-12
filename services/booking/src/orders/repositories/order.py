import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.consistency import ReplicaReads
from core.db import ReplicaSessionDep, SessionDep
from orders.models.order import FailureReason, Order, OrderStatus, SagaStep
from orders.models.payment_token import OrderPaymentToken


class OrderRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def get_for_user(self, *, order_id: uuid.UUID, user_id: str) -> Order | None:
        result = await self.session.execute(
            select(Order).where(Order.id == order_id, Order.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_key(self, *, user_id: str, idempotency_key: str) -> Order | None:
        result = await self.session.execute(
            select(Order).where(
                Order.user_id == user_id, Order.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def insert_pending_if_absent(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        hold_id: uuid.UUID,
        payment_token: str,
        request_fingerprint: str,
    ) -> None:
        """No conflict target on purpose: either unique index refuses the row.
        Of two concurrent calls one inserts, the other waits for it and does nothing.
        One statement writes the token next to the order, only for an order it created,
        so a repeat adds nothing.
        """
        new_order = (
            insert(Order)
            .values(
                user_id=user_id,
                idempotency_key=idempotency_key,
                hold_id=hold_id,
                request_fingerprint=request_fingerprint,
                status=OrderStatus.PENDING,
            )
            .on_conflict_do_nothing()
            .returning(Order.id)
            .cte("new_order")
        )
        await self.session.execute(
            insert(OrderPaymentToken)
            .from_select(
                ["order_id", "payment_token"],
                select(new_order.c.id, literal(payment_token)),
            )
            .add_cte(new_order)
        )

    async def payment_token_of(self, *, order: Order) -> str:
        """Every order in flight has a token row. A missing one is a bug: the step fails
        and is retried, rather than charging with a guess."""
        token = await self.session.scalar(
            select(OrderPaymentToken.payment_token).where(
                OrderPaymentToken.order_id == order.id
            )
        )
        if token is None:
            raise LookupError(f"Order {order.id} has no payment token")
        return token

    async def lock_next_due(self) -> Order | None:
        """An order another worker is on is skipped. The lock ends with the transaction,
        so a crash frees the order at once.
        """
        result = await self.session.execute(
            select(Order)
            .where(
                Order.saga_step.in_(
                    [
                        SagaStep.STARTED,
                        SagaStep.AWAITING_CHARGE,
                        SagaStep.CHARGED,
                        SagaStep.AWAITING_REFUND,
                    ]
                ),
                Order.next_attempt_at <= func.now(),
            )
            .order_by(Order.next_attempt_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def lock(self, *, order_id: uuid.UUID) -> Order | None:
        """Waits for a worker on the same order, so a reply sees the finished step."""
        result = await self.session.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def mark_awaiting_charge(
        self,
        *,
        order_id: uuid.UUID,
        amount_minor: int,
        currency: str,
        reply_wait_seconds: float,
    ) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(
                saga_step=SagaStep.AWAITING_CHARGE,
                amount_minor=amount_minor,
                currency=currency,
                attempts=0,
                next_attempt_at=func.now() + timedelta(seconds=reply_wait_seconds),
            )
        )

    async def mark_charged(self, *, order_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(saga_step=SagaStep.CHARGED, attempts=0, next_attempt_at=func.now())
        )

    async def mark_confirmed(self, *, order_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(status=OrderStatus.CONFIRMED, saga_step=SagaStep.DONE)
        )
        await self._forget_payment_token(order_id=order_id)

    async def mark_awaiting_refund(
        self,
        *,
        order_id: uuid.UUID,
        reason: FailureReason,
        reply_wait_seconds: float,
    ) -> None:
        """The reason is stored now: the reply of payment does not carry it."""
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(
                saga_step=SagaStep.AWAITING_REFUND,
                failure_reason=reason,
                attempts=0,
                next_attempt_at=func.now() + timedelta(seconds=reply_wait_seconds),
            )
        )

    async def mark_refund_finished(self, *, order_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(status=OrderStatus.FAILED, saga_step=SagaStep.DONE)
        )
        await self._forget_payment_token(order_id=order_id)

    async def mark_failed(self, *, order_id: uuid.UUID, reason: FailureReason) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(
                status=OrderStatus.FAILED,
                saga_step=SagaStep.DONE,
                failure_reason=reason,
            )
        )
        await self._forget_payment_token(order_id=order_id)

    async def _forget_payment_token(self, *, order_id: uuid.UUID) -> None:
        """A final order never charges again, so it keeps no token. In the transaction
        that makes it final, so a crash cannot leave one behind."""
        await self.session.execute(
            delete(OrderPaymentToken).where(OrderPaymentToken.order_id == order_id)
        )

    async def postpone(
        self, *, order_id: uuid.UUID, attempts: int, delay_seconds: float
    ) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(
                attempts=attempts,
                next_attempt_at=func.now() + timedelta(seconds=delay_seconds),
            )
        )


def get_order_repository(session: SessionDep) -> OrderRepository:
    return OrderRepository(session=session)


OrderRepositoryDep = Annotated[OrderRepository, Depends(get_order_repository)]


def get_replica_order_repository(session: ReplicaSessionDep) -> OrderRepository:
    """Reads from the replica: what was committed up to the replica's lag ago."""
    return OrderRepository(session=session)


ReplicaOrderRepositoryDep = Annotated[
    OrderRepository, Depends(get_replica_order_repository)
]


def get_order_reads(
    replica_session: ReplicaSessionDep,
    replica: ReplicaOrderRepositoryDep,
    primary: OrderRepositoryDep,
) -> ReplicaReads[OrderRepository]:
    return ReplicaReads(
        replica_session=replica_session,
        replica=replica,
        primary=primary,
    )


OrderReadsDep = Annotated[ReplicaReads[OrderRepository], Depends(get_order_reads)]
