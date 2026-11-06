"""Runs one charge in a process of its own, so a crash token really kills it."""

import asyncio
import sys
import uuid

from core.db import async_session
from core.settings import settings
from payments.dto.payment import ChargeRequest
from payments.repositories.charge import ChargeRepository
from payments.services.provider import MockProvider
from payments.use_cases.charge import ChargeUseCase


async def main(order_id: str, token: str) -> None:
    async with async_session() as session:
        use_case = ChargeUseCase(
            session=session,
            charge_repository=ChargeRepository(session=session),
            provider=MockProvider(faults=settings.FAULTS),
        )
        await use_case.execute(
            request=ChargeRequest(
                order_id=uuid.UUID(order_id),
                amount_minor=10_000,
                currency="UAH",
                payment_token=token,
            )
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
