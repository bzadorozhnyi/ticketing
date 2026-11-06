import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from core.exceptions import InternalServerException
from payments.dto.payment import RefundStatus
from payments.models.charge import ChargeStatus
from payments.repositories.charge import ChargeRepository, ChargeRepositoryDep
from payments.repositories.refund import RefundRepository, RefundRepositoryDep


class RefundUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        charge_repository: ChargeRepository,
        refund_repository: RefundRepository,
    ):
        self.session = session
        self.charge_repository = charge_repository
        self.refund_repository = refund_repository

    async def execute(
        self, *, order_id: uuid.UUID, amount_minor: int, currency: str
    ) -> RefundStatus:
        """Safe to repeat. A refund can overtake its charge in the queue, so it leaves
        a CANCELLED row: the unique `order_id` then stops the late charge.
        """
        cancelled_first = await self.charge_repository.insert_if_absent(
            order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
            status=ChargeStatus.CANCELLED,
        )
        if cancelled_first:
            await self.session.commit()
            return RefundStatus.NOTHING_TO_REFUND

        charge = await self.charge_repository.get_by_order(order_id=order_id)
        if charge is None:
            raise InternalServerException(f"Charge for {order_id} is missing")
        if charge.status != ChargeStatus.SUCCEEDED:
            return RefundStatus.NOTHING_TO_REFUND

        await self.refund_repository.insert_if_absent(charge_id=charge.id)
        await self.session.commit()
        return RefundStatus.REFUNDED


def get_refund_use_case(
    session: SessionDep,
    charge_repository: ChargeRepositoryDep,
    refund_repository: RefundRepositoryDep,
) -> RefundUseCase:
    return RefundUseCase(
        session=session,
        charge_repository=charge_repository,
        refund_repository=refund_repository,
    )


RefundUseCaseDep = Annotated[RefundUseCase, Depends(get_refund_use_case)]
