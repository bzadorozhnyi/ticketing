import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from payments.models.charge import Charge, ChargeStatus
from payments.models.refund import Refund


class ChargeRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def get_by_order(self, *, order_id: uuid.UUID) -> Charge | None:
        result = await self.session.execute(
            select(Charge).where(Charge.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def insert_if_absent(
        self,
        *,
        order_id: uuid.UUID,
        amount_minor: int,
        currency: str,
        status: str,
        payment_token: str | None = None,
    ) -> bool:
        """The unique `order_id` decides the race: of two concurrent calls one inserts,
        the other waits for it and gets False.
        """
        result = await self.session.execute(
            insert(Charge)
            .values(
                order_id=order_id,
                amount_minor=amount_minor,
                currency=currency,
                status=status,
                payment_token=payment_token,
            )
            .on_conflict_do_nothing(index_elements=[Charge.order_id])
            .returning(Charge.id)
        )
        return result.scalar_one_or_none() is not None

    async def taken_without_refund(
        self, *, older_than: timedelta, newer_than: timedelta
    ) -> list[uuid.UUID]:
        """Orders of charges that took money and were not refunded, created within the
        window, by the database's clock.
        """
        result = await self.session.scalars(
            select(Charge.order_id)
            .where(
                Charge.status == ChargeStatus.SUCCEEDED,
                Charge.created_at < func.now() - older_than,
                Charge.created_at >= func.now() - newer_than,
                ~exists().where(Refund.charge_id == Charge.id),
            )
            .order_by(Charge.created_at)
        )
        return list(result)

    async def finish(self, *, order_id: uuid.UUID, status: str) -> None:
        await self.session.execute(
            update(Charge).where(Charge.order_id == order_id).values(status=status)
        )


def get_charge_repository(session: SessionDep) -> ChargeRepository:
    return ChargeRepository(session=session)


ChargeRepositoryDep = Annotated[ChargeRepository, Depends(get_charge_repository)]
