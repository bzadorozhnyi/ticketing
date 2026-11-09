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
from payments.services.pending_charge import (
    PendingChargeFinisher,
    PendingChargeFinisherDep,
    refund_key,
)
from payments.services.provider import MockProvider, MockProviderDep


class RefundUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        charge_repository: ChargeRepository,
        refund_repository: RefundRepository,
        finisher: PendingChargeFinisher,
        provider: MockProvider,
    ):
        self.session = session
        self.charge_repository = charge_repository
        self.refund_repository = refund_repository
        self.finisher = finisher
        self.provider = provider

    async def execute(
        self, *, order_id: uuid.UUID, amount_minor: int, currency: str
    ) -> RefundStatus:
        """Safe to repeat. A refund can overtake its charge in the queue, so it leaves
        a CANCELLED row: the unique `order_id` then stops the late charge.

        A PENDING charge may have taken the money already, and nobody else may finish
        it, so the refund finishes it first with the same provider key. The refund is
        stored before the provider call and repeats it with its own key on every run.
        """
        cancelled_first = await self.charge_repository.insert_if_absent(
            order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
            status=ChargeStatus.CANCELLED,
        )
        charge = await self.charge_repository.get_by_order(order_id=order_id)
        await self.session.commit()
        if cancelled_first:
            return RefundStatus.NOTHING_TO_REFUND
        if charge is None:
            raise InternalServerException(f"Charge for {order_id} is missing")

        if charge.status == ChargeStatus.PENDING:
            charge = await self.finisher.finish(charge=charge)
        if charge.status != ChargeStatus.SUCCEEDED:
            return RefundStatus.NOTHING_TO_REFUND

        await self.refund_repository.insert_if_absent(charge_id=charge.id)
        await self.session.commit()
        await self.provider.refund(
            idempotency_key=refund_key(order_id=order_id),
            amount_minor=charge.amount_minor,
            currency=charge.currency,
        )
        return RefundStatus.REFUNDED


def get_refund_use_case(
    session: SessionDep,
    charge_repository: ChargeRepositoryDep,
    refund_repository: RefundRepositoryDep,
    finisher: PendingChargeFinisherDep,
    provider: MockProviderDep,
) -> RefundUseCase:
    return RefundUseCase(
        session=session,
        charge_repository=charge_repository,
        refund_repository=refund_repository,
        finisher=finisher,
        provider=provider,
    )


RefundUseCaseDep = Annotated[RefundUseCase, Depends(get_refund_use_case)]
