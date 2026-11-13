from core.rabbitmq import RabbitMQClient
from payments.dto.messages import ChargeResult, RefundResult


class PaymentRepliesQueue:
    """Results of commands, for booking. A reply booking rejects, or one delivered 20
    times, goes to the dead letter queue: a broker policy says so
    (infra/rabbitmq/policies.sh), since the queue existed before it had dead letters.
    """

    def __init__(self, *, client: RabbitMQClient):
        self.client = client
        self.name = f"{client.settings.QUEUE_PREFIX}.replies"
        self.dead_letter_name = f"{self.name}.dead"

    async def declare(self) -> None:
        await self.client.declare_queue(name=self.dead_letter_name)
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
