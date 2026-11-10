import asyncio
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime

from loguru import logger
from opentelemetry.trace import SpanKind
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.faults import crash_point
from core.rabbitmq import RabbitMQClient
from core.settings import OutboxSettings
from core.telemetry import (
    TRACEPARENT,
    continue_trace,
    current_traceparent,
    outbox_publish_delay,
)
from outbox.models.message import OutboxMessage
from outbox.repositories.message import OutboxRepository


class Relay:
    """Publishes the outbox. A row is deleted only after the broker confirmed it, so a
    crash can repeat a message but never lose it: delivery is at least once.
    """

    def __init__(
        self,
        *,
        client: RabbitMQClient,
        session_maker: async_sessionmaker[AsyncSession],
        settings: OutboxSettings,
    ):
        self.client = client
        self.session_maker = session_maker
        self.settings = settings

    async def run(self, *, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                published = await self.publish_batch()
            except Exception as exc:
                logger.bind(error=repr(exc)).warning("Outbox batch failed")
                published = 0
            if published < self.settings.BATCH_SIZE:
                with suppress(TimeoutError):
                    async with asyncio.timeout(self.settings.POLL_INTERVAL_SECONDS):
                        await stop.wait()

    async def publish_batch(self) -> int:
        async with self.session_maker() as session:
            repository = OutboxRepository(session=session)
            messages = await repository.lock_batch(limit=self.settings.BATCH_SIZE)
            published = await self._publish_until_failure(messages=messages)
            await repository.delete(ids=published)
            await session.commit()
        return len(published)

    async def _publish_until_failure(
        self, *, messages: Sequence[OutboxMessage]
    ) -> list[uuid.UUID]:
        """Stops at the first failure and keeps what the broker has confirmed:
        a rollback of the whole batch would publish those messages again on every pass.
        """
        published: list[uuid.UUID] = []
        for message in messages:
            try:
                await self._publish(message=message)
            except Exception as exc:
                logger.bind(error=repr(exc), queue=message.queue).warning(
                    "Publish failed, the message stays in the outbox"
                )
                break
            waited = datetime.now(UTC) - message.created_at
            outbox_publish_delay.record(
                waited.total_seconds(), {"messaging.destination.name": message.queue}
            )
            crash_point("after_publish")
            published.append(message.id)
        return published

    async def _publish(self, *, message: OutboxMessage) -> None:
        """A producer span in the trace of the step that wrote the message; the message
        carries this span's `traceparent`, so the consumer's span is its child."""
        name = f"publish {message.queue}"
        with continue_trace(message.traceparent, name=name, kind=SpanKind.PRODUCER):
            traceparent = current_traceparent()
            async with asyncio.timeout(self.settings.PUBLISH_TIMEOUT_SECONDS):
                await self.client.publish(
                    queue=message.queue,
                    body=message.body.encode(),
                    headers={TRACEPARENT: traceparent} if traceparent else None,
                )
