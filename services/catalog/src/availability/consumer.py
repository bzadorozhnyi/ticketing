import asyncio
from typing import Literal

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from availability.dto.seat_event import SeatEventDTO
from availability.repositories.seat_availability import SeatAvailabilityRepository
from availability.use_cases.apply_seat_event import ApplySeatEventUseCase
from core.rabbitmq import Delivery
from core.settings import RabbitMQSettings
from core.telemetry import seat_events

Outcome = Literal["applied", "stale", "retry", "malformed"]


class SeatEventConsumer:
    """Applies, commits, then acknowledges: a crash at any point delivers the event
    again, and a version already stored makes the repeat change nothing.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        settings: RabbitMQSettings,
    ):
        self.session_maker = session_maker
        self.settings = settings

    async def handle(self, delivery: Delivery) -> None:
        try:
            event = SeatEventDTO.model_validate_json(delivery.body)
        except ValidationError as exc:
            logger.bind(error=repr(exc)).error("Malformed seat event")
            await delivery.dead_letter()
            self._count("malformed")
            return

        try:
            async with asyncio.timeout(self.settings.HANDLER_TIMEOUT_SECONDS):
                changed = await self._apply(event=event)
        except Exception as exc:
            logger.bind(error=repr(exc)).warning("Seat event failed, will retry")
            await self._retry_later(delivery=delivery)
            self._count("retry")
        else:
            await delivery.ack()
            self._count("applied" if changed else "stale")

    async def _apply(self, *, event: SeatEventDTO) -> int:
        async with self.session_maker() as session:
            use_case = ApplySeatEventUseCase(
                session=session, repository=SeatAvailabilityRepository(session=session)
            )
            return await use_case.execute(event=event)

    async def _retry_later(self, *, delivery: Delivery) -> None:
        """Without a pause an outage of a few seconds would burn the delivery limit
        in milliseconds and send every event to the dead letter queue.
        """
        delay = self.settings.RETRY_BASE_DELAY_SECONDS * 2**delivery.attempts
        await asyncio.sleep(min(delay, self.settings.MAX_RETRY_DELAY_SECONDS))
        await delivery.retry()

    @staticmethod
    def _count(outcome: Outcome) -> None:
        seat_events.add(1, {"outcome": outcome})
