import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from payments.models.charge import Charge


class ChargeRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def get_by_order(self, *, order_id: uuid.UUID) -> Charge | None:
        result = await self.session.execute(
            select(Charge).where(Charge.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def insert_if_absent(
        self, *, order_id: uuid.UUID, amount_minor: int, currency: str, status: str
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
            )
            .on_conflict_do_nothing(index_elements=[Charge.order_id])
            .returning(Charge.id)
        )
        return result.scalar_one_or_none() is not None


def get_charge_repository(session: SessionDep) -> ChargeRepository:
    return ChargeRepository(session=session)


ChargeRepositoryDep = Annotated[ChargeRepository, Depends(get_charge_repository)]
