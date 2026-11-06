from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from core.exceptions import ConflictException, InternalServerException
from payments.dto.payment import ChargeDTO, ChargeRequest
from payments.models.charge import Charge
from payments.repositories.charge import ChargeRepository, ChargeRepositoryDep
from payments.services.provider import MockProvider, MockProviderDep


class ChargeUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        charge_repository: ChargeRepository,
        provider: MockProvider,
    ):
        self.session = session
        self.charge_repository = charge_repository
        self.provider = provider

    async def execute(self, *, request: ChargeRequest) -> ChargeDTO:
        """Safe to repeat: the first call decides, every repeat returns its result.

        A crash before the commit leaves nothing, so a repeat charges for the first
        time. A crash after it leaves the row, so a repeat takes no money.
        """
        existing = await self.charge_repository.get_by_order(order_id=request.order_id)
        if existing is not None:
            return self._repeat(existing=existing, request=request)

        status = await self.provider.authorize(token=request.payment_token)
        inserted = await self.charge_repository.insert_if_absent(
            order_id=request.order_id,
            amount_minor=request.amount_minor,
            currency=request.currency,
            status=status,
        )
        await self.session.commit()
        self.provider.after_commit(token=request.payment_token)

        charge = await self.charge_repository.get_by_order(order_id=request.order_id)
        if charge is None:
            raise InternalServerException(f"Charge for {request.order_id} is missing")
        if not inserted:
            return self._repeat(existing=charge, request=request)
        return ChargeDTO.from_model(charge)

    def _repeat(self, *, existing: Charge, request: ChargeRequest) -> ChargeDTO:
        same_request = (
            existing.amount_minor == request.amount_minor
            and existing.currency == request.currency
        )
        if not same_request:
            raise ConflictException(
                f"Order {request.order_id} was charged with another amount"
            )
        return ChargeDTO.from_model(existing)


def get_charge_use_case(
    session: SessionDep,
    charge_repository: ChargeRepositoryDep,
    provider: MockProviderDep,
) -> ChargeUseCase:
    return ChargeUseCase(
        session=session, charge_repository=charge_repository, provider=provider
    )


ChargeUseCaseDep = Annotated[ChargeUseCase, Depends(get_charge_use_case)]
