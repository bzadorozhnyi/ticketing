"""Background job of the worker: every COHERENCE__INTERVAL_SECONDS it looks for charges
that took money for an order booking does not have, exports their number and logs them.
"""

import asyncio
from contextlib import suppress

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.settings import CoherenceSettings
from core.telemetry import charge_checks, set_charges_without_order
from payments.clients.booking import BookingClient
from payments.repositories.charge import ChargeRepository
from payments.use_cases.find_charges_without_order import FindChargesWithoutOrderUseCase


async def check_once(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    booking: BookingClient,
    settings: CoherenceSettings,
) -> None:
    """A failed check exports no count: "unknown" must not read as "none"."""
    try:
        async with session_maker() as session:
            missing = await FindChargesWithoutOrderUseCase(
                session=session,
                charge_repository=ChargeRepository(session=session),
                booking=booking,
                settings=settings,
            ).execute()
    except Exception as exc:
        set_charges_without_order(None)
        charge_checks.add(1, {"outcome": "failed"})
        logger.bind(error=repr(exc)).warning("Charge check failed")
        return
    set_charges_without_order(len(missing))
    charge_checks.add(1, {"outcome": "ok"})
    if missing:
        logger.bind(order_ids=[str(order_id) for order_id in missing[:20]]).error(
            "{} charges kept money for orders booking does not have", len(missing)
        )


async def run(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    settings: CoherenceSettings,
    stop: asyncio.Event,
) -> None:
    if settings.BOOKING_URL is None:
        return
    booking = BookingClient(
        base_url=settings.BOOKING_URL, timeout_seconds=settings.TIMEOUT_SECONDS
    )
    while not stop.is_set():
        await check_once(
            session_maker=session_maker, booking=booking, settings=settings
        )
        with suppress(TimeoutError):
            async with asyncio.timeout(settings.INTERVAL_SECONDS):
                await stop.wait()
