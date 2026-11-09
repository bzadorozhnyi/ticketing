from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from payments.models.provider_operation import ProviderOperation


class ProviderOperationRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def insert_if_absent(
        self,
        *,
        idempotency_key: str,
        kind: str,
        amount_minor: int,
        currency: str,
        status: str,
    ) -> None:
        """A concurrent call with the same key waits for the first and does nothing."""
        await self.session.execute(
            insert(ProviderOperation)
            .values(
                idempotency_key=idempotency_key,
                kind=kind,
                amount_minor=amount_minor,
                currency=currency,
                status=status,
            )
            .on_conflict_do_nothing(index_elements=[ProviderOperation.idempotency_key])
        )

    async def get_by_key(self, *, idempotency_key: str) -> ProviderOperation | None:
        result = await self.session.execute(
            select(ProviderOperation).where(
                ProviderOperation.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()
