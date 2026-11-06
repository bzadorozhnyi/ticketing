import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from orders.models.order import FailureReason, Order, OrderStatus, SagaStep


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
    ) -> None:
        """No conflict target on purpose: either unique index refuses the row.
        Of two concurrent calls one inserts, the other waits for it and does nothing.
        """
        await self.session.execute(
            insert(Order)
            .values(
                user_id=user_id,
                idempotency_key=idempotency_key,
                hold_id=hold_id,
                payment_token=payment_token,
                status=OrderStatus.PENDING,
            )
            .on_conflict_do_nothing()
        )

    async def lock_next_due(self) -> Order | None:
        """An order another worker is on is skipped. The lock ends with the transaction,
        so a crash frees the order at once.
        """
        result = await self.session.execute(
            select(Order)
            .where(
                Order.saga_step.in_([SagaStep.STARTED, SagaStep.CHARGED]),
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
        self, *, order_id: uuid.UUID, amount_minor: int, currency: str
    ) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(
                saga_step=SagaStep.AWAITING_CHARGE,
                amount_minor=amount_minor,
                currency=currency,
                attempts=0,
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

    async def mark_awaiting_refund(self, *, order_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(saga_step=SagaStep.AWAITING_REFUND, attempts=0)
        )

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
