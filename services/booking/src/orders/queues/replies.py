from collections.abc import Awaitable, Callable

from core.rabbitmq import Delivery, RabbitMQClient, Subscription


class PaymentRepliesQueue:
    """Results of payment commands. Declared here too, with the same arguments as in
    payment, so booking can start first.
    """

    def __init__(self, *, client: RabbitMQClient, name: str):
        self.client = client
        self.name = name

    async def declare(self) -> None:
        await self.client.declare_queue(name=self.name)

    async def consume(
        self, *, handler: Callable[[Delivery], Awaitable[None]]
    ) -> Subscription:
        return await self.client.consume(queue=self.name, handler=handler)
