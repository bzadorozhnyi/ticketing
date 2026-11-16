import uuid
from datetime import timedelta
from itertools import batched

from sqlalchemy.ext.asyncio import AsyncSession

from core.settings import CoherenceSettings
from payments.clients.booking import BookingClient
from payments.repositories.charge import ChargeRepository


class FindChargesWithoutOrderUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        charge_repository: ChargeRepository,
        booking: BookingClient,
        settings: CoherenceSettings,
    ):
        self.session = session
        self.charge_repository = charge_repository
        self.booking = booking
        self.settings = settings

    async def execute(self) -> list[uuid.UUID]:
        """Orders whose money payment took and kept while booking has no such order.

        booking commits an order before it sends the charge command, so every charge
        should find its order; one that does not was lost on booking's side (ADR 0013).
        It only reports: deciding about the money is for a person. The connection is
        given back before booking is asked.
        """
        taken = await self.charge_repository.taken_without_refund(
            older_than=timedelta(seconds=self.settings.GRACE_SECONDS),
            newer_than=timedelta(seconds=self.settings.LOOKBACK_SECONDS),
        )
        await self.session.rollback()
        missing: list[uuid.UUID] = []
        for batch in batched(taken, self.settings.BATCH_SIZE, strict=False):
            existing = await self.booking.existing(order_ids=list(batch))
            missing += [order_id for order_id in batch if order_id not in existing]
        return missing
