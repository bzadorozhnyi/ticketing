import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from payments.models.refund import Refund


class RefundRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def insert_if_absent(self, *, charge_id: uuid.UUID) -> None:
        await self.session.execute(
            insert(Refund)
            .values(charge_id=charge_id)
            .on_conflict_do_nothing(index_elements=[Refund.charge_id])
        )


def get_refund_repository(session: SessionDep) -> RefundRepository:
    return RefundRepository(session=session)


RefundRepositoryDep = Annotated[RefundRepository, Depends(get_refund_repository)]
