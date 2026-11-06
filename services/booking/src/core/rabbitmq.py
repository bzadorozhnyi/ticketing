from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import (
    AbstractChannel,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
    ConsumerTag,
    HeadersType,
)

from core.settings import RabbitMQSettings


class Delivery:
    """A received message. Only this class knows how RabbitMQ settles a message."""

    def __init__(self, *, message: AbstractIncomingMessage):
        self._message = message

    @property
    def body(self) -> bytes:
        return self._message.body

    @property
    def redelivered(self) -> bool:
        return bool(self._message.redelivered)

    @property
    def attempts(self) -> int:
        attempts = self._message.headers.get("x-delivery-count", 0)
        return attempts if isinstance(attempts, int) else 0

    @property
    def traceparent(self) -> str | None:
        traceparent = self._message.headers.get("traceparent")
        return traceparent if isinstance(traceparent, str) else None

    async def ack(self) -> None:
        await self._message.ack()

    async def retry(self) -> None:
        """`reject`, not `nack`: on RabbitMQ 4.4 a requeue through `nack` does not count
        towards the delivery limit, so a failing message would circle forever.
        """
        await self._message.reject(requeue=True)

    async def dead_letter(self) -> None:
        await self._message.reject(requeue=False)


class Subscription:
    """A running consumer of one queue."""

    def __init__(self, *, queue: AbstractQueue, tag: ConsumerTag):
        self._queue = queue
        self._tag = tag

    async def stop(self) -> None:
        await self._queue.cancel(self._tag)


class RabbitMQClient:
    """The connection and the basic operations. A class per queue is built on top of it,
    so the rest of the code never sees the broker library.
    """

    def __init__(self, *, settings: RabbitMQSettings):
        self.settings = settings
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None

    @property
    def channel(self) -> AbstractChannel:
        if self._channel is None:
            raise RuntimeError("RabbitMQClient is not connected")
        return self._channel

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self.settings.url)
        self._channel = await self._connection.channel(on_return_raises=True)
        await self._channel.set_qos(prefetch_count=self.settings.PREFETCH_COUNT)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()

    async def declare_queue(
        self, *, name: str, arguments: dict[str, Any] | None = None
    ) -> None:
        """Every queue is durable and quorum."""
        await self.channel.declare_queue(
            name,
            durable=True,
            arguments={"x-queue-type": "quorum", **(arguments or {})},
        )

    async def publish(
        self,
        *,
        queue: str,
        body: bytes,
        headers: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Returns once the broker has confirmed the message."""
        message_headers: HeadersType | None = dict(headers) if headers else None
        await self.channel.default_exchange.publish(
            Message(
                body=body,
                content_type="application/json",
                delivery_mode=DeliveryMode.PERSISTENT,
                correlation_id=correlation_id,
                headers=message_headers,
            ),
            routing_key=queue,
            mandatory=True,
        )

    async def consume(
        self, *, queue: str, handler: Callable[[Delivery], Awaitable[None]]
    ) -> Subscription:
        async def on_message(message: AbstractIncomingMessage) -> None:
            await handler(Delivery(message=message))

        declared = await self.channel.get_queue(queue)
        tag = await declared.consume(on_message)
        return Subscription(queue=declared, tag=tag)
