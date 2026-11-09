import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from core.exceptions import InternalServerException
from payments.models.charge import Charge
from payments.repositories.charge import ChargeRepository, ChargeRepositoryDep
from payments.services.provider import MockProvider, MockProviderDep


def charge_key(*, order_id: uuid.UUID) -> str:
    return f"charge:{order_id}"


def refund_key(*, order_id: uuid.UUID) -> str:
    return f"refund:{order_id}"


class PendingChargeFinisher:
    """Finishes a pending charge: asks the provider with the order's key and stores the
    answer. Safe to run twice at once, or after a crash: the provider returns the first
    answer for the key and takes no money again, so every finisher stores the same
    status.
    """

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

    async def finish(self, *, charge: Charge) -> Charge:
        """The caller has committed, so no database connection is held while the
        provider answers.
        """
        if charge.payment_token is None:
            raise InternalServerException(f"Charge {charge.id} has no token")
        status = await self.provider.charge(
            idempotency_key=charge_key(order_id=charge.order_id),
            token=charge.payment_token,
            amount_minor=charge.amount_minor,
            currency=charge.currency,
        )
        await self.charge_repository.finish(order_id=charge.order_id, status=status)
        finished = await self.charge_repository.get_by_order(order_id=charge.order_id)
        await self.session.commit()
        if finished is None:
            raise InternalServerException(f"Charge {charge.id} is missing")
        return finished


def get_pending_charge_finisher(
    session: SessionDep,
    charge_repository: ChargeRepositoryDep,
    provider: MockProviderDep,
) -> PendingChargeFinisher:
    return PendingChargeFinisher(
        session=session, charge_repository=charge_repository, provider=provider
    )


PendingChargeFinisherDep = Annotated[
    PendingChargeFinisher, Depends(get_pending_charge_finisher)
]
