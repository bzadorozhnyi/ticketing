from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from core.exceptions import ConflictException, InternalServerException
from payments.dto.payment import ChargeDTO, ChargeRequest
from payments.models.charge import Charge, ChargeStatus
from payments.repositories.charge import ChargeRepository, ChargeRepositoryDep
from payments.services.pending_charge import (
    PendingChargeFinisher,
    PendingChargeFinisherDep,
)
from payments.services.provider import MockProvider, MockProviderDep


class ChargeUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        charge_repository: ChargeRepository,
        finisher: PendingChargeFinisher,
        provider: MockProvider,
    ):
        self.session = session
        self.charge_repository = charge_repository
        self.finisher = finisher
        self.provider = provider

    async def execute(self, *, request: ChargeRequest) -> ChargeDTO:
        """Safe to repeat. The charge is reserved as PENDING and committed before the
        provider is called, with the order as the provider's idempotency key.

        A crash before the provider answers, or after it and before our commit, leaves
        PENDING: a repeat asks the provider again with the same key and gets the first
        answer, so money is taken once. A repeat of a finished charge returns it.
        """
        await self.charge_repository.insert_if_absent(
            order_id=request.order_id,
            amount_minor=request.amount_minor,
            currency=request.currency,
            status=ChargeStatus.PENDING,
            payment_token=request.payment_token,
        )
        charge = await self.charge_repository.get_by_order(order_id=request.order_id)
        await self.session.commit()
        if charge is None:
            raise InternalServerException(f"Charge for {request.order_id} is missing")
        self._check_terms(charge=charge, request=request)

        if charge.status != ChargeStatus.PENDING:
            return ChargeDTO.from_model(charge)
        finished = await self.finisher.finish(charge=charge)
        self.provider.after_commit(token=request.payment_token)
        return ChargeDTO.from_model(finished)

    def _check_terms(self, *, charge: Charge, request: ChargeRequest) -> None:
        same_request = (
            charge.amount_minor == request.amount_minor
            and charge.currency == request.currency
        )
        if not same_request:
            raise ConflictException(
                f"Order {request.order_id} was charged with another amount"
            )


def get_charge_use_case(
    session: SessionDep,
    charge_repository: ChargeRepositoryDep,
    finisher: PendingChargeFinisherDep,
    provider: MockProviderDep,
) -> ChargeUseCase:
    return ChargeUseCase(
        session=session,
        charge_repository=charge_repository,
        finisher=finisher,
        provider=provider,
    )


ChargeUseCaseDep = Annotated[ChargeUseCase, Depends(get_charge_use_case)]
