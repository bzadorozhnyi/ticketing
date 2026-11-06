from collections.abc import Awaitable, Callable

from core.rabbitmq import Delivery, RabbitMQClient, Subscription


class PaymentCommandsQueue:
    """Commands for the payment service. A command over the delivery limit goes to the
    dead letter queue instead of being dropped.
    """

    def __init__(self, *, client: RabbitMQClient):
        self.client = client
        self.name = f"{client.settings.QUEUE_PREFIX}.commands"
        self.dead_letter_name = f"{self.name}.dead"

    async def declare(self) -> None:
        await self.client.declare_queue(name=self.dead_letter_name)
        await self.client.declare_queue(
            name=self.name,
            arguments={
                "x-delivery-limit": self.client.settings.DELIVERY_LIMIT,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": self.dead_letter_name,
            },
        )

    async def consume(
        self, *, handler: Callable[[Delivery], Awaitable[None]]
    ) -> Subscription:
        return await self.client.consume(queue=self.name, handler=handler)
