import asyncio
import uuid
from collections.abc import Sequence
from contextlib import suppress

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.faults import crash_point
from core.rabbitmq import RabbitMQClient
from core.settings import OutboxSettings
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
                async with asyncio.timeout(self.settings.PUBLISH_TIMEOUT_SECONDS):
                    await self.client.publish(
                        queue=message.queue,
                        body=message.body.encode(),
                        headers=(
                            {"traceparent": message.traceparent}
                            if message.traceparent
                            else None
                        ),
                    )
            except Exception as exc:
                logger.bind(error=repr(exc), queue=message.queue).warning(
                    "Publish failed, the message stays in the outbox"
                )
                break
            crash_point("after_publish")
            published.append(message.id)
        return published
