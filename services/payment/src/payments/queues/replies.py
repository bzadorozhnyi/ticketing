from core.rabbitmq import RabbitMQClient
from payments.dto.messages import ChargeResult, RefundResult


class PaymentRepliesQueue:
    """Results of commands, for booking."""

    def __init__(self, *, client: RabbitMQClient):
        self.client = client
        self.name = f"{client.settings.QUEUE_PREFIX}.replies"

    async def declare(self) -> None:
        await self.client.declare_queue(name=self.name)

    async def send(
        self, *, result: ChargeResult | RefundResult, traceparent: str | None
    ) -> None:
        await self.client.publish(
            queue=self.name,
            body=result.model_dump_json().encode(),
            headers={"traceparent": traceparent} if traceparent else None,
            correlation_id=str(result.order_id),
        )
