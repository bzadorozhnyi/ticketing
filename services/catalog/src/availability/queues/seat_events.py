from collections.abc import Awaitable, Callable

from core.rabbitmq import Delivery, RabbitMQClient, Subscription

EXCHANGE = "seat-hold.events"


class SeatEventsQueue:
    """catalog's own queue of seat-hold's events (ADR 0014). It receives only what is
    published after it is bound; a message over the delivery limit goes to the dead
    letter queue by a broker policy.
    """

    def __init__(self, *, client: RabbitMQClient, name: str = "catalog.seat-events"):
        self.client = client
        self.name = name
        self.dead_letter_name = f"{self.name}.dead"

    async def declare(self) -> None:
        """The exchange too, with seat-hold's arguments, so the projector can start
        before seat-hold's relay ever has.
        """
        await self.client.declare_topic_exchange(name=EXCHANGE)
        await self.client.declare_queue(name=self.dead_letter_name)
        await self.client.declare_queue(name=self.name)
        await self.client.bind_queue(
            queue=self.name, exchange=EXCHANGE, routing_key="seats.#"
        )

    async def consume(
        self, *, handler: Callable[[Delivery], Awaitable[None]]
    ) -> Subscription:
        return await self.client.consume(queue=self.name, handler=handler)
