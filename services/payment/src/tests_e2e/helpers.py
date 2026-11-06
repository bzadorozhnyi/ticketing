import asyncio
import json
import uuid
from collections.abc import Iterable

from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractChannel, AbstractQueue
from sqlalchemy import func, select

from payments.models.charge import Charge
from payments.models.refund import Refund


def charge_command(order_id: uuid.UUID, token: str = "tok_ok", amount: int = 10_000):
    return {
        "type": "charge",
        "order_id": str(order_id),
        "amount_minor": amount,
        "currency": "UAH",
        "payment_token": token,
    }


def refund_command(order_id: uuid.UUID):
    return {
        "type": "refund",
        "order_id": str(order_id),
        "amount_minor": 10_000,
        "currency": "UAH",
    }


class Broker:
    """Queues with a unique prefix, so tests never see each other's messages."""

    def __init__(self, *, channel: AbstractChannel, prefix: str):
        self.channel = channel
        self.prefix = prefix
        self.commands_name = f"{prefix}.commands"
        self.replies_name = f"{prefix}.replies"
        self.dead_name = f"{prefix}.commands.dead"

    async def attach(self) -> None:
        """The queues are declared by the code under test."""
        self.commands = await self.channel.get_queue(self.commands_name)
        self.replies = await self.channel.get_queue(self.replies_name)
        self.dead = await self.channel.get_queue(self.dead_name)

    async def publish(self, body: dict | bytes, headers: dict | None = None) -> None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        await self.channel.default_exchange.publish(
            Message(
                body=payload, delivery_mode=DeliveryMode.PERSISTENT, headers=headers
            ),
            routing_key=self.commands_name,
            mandatory=True,
        )

    async def take(self, queue: AbstractQueue, *, timeout: float = 5):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            message = await queue.get(fail=False)
            if message is not None:
                await message.ack()
                return message
            await asyncio.sleep(0.02)
        return None

    async def take_all(self, queue: AbstractQueue, *, wait: float = 0.3) -> list:
        messages = []
        while (message := await self.take(queue, timeout=wait)) is not None:
            messages.append(message)
        return messages

    async def delete(self) -> None:
        for name in (self.commands_name, self.replies_name, self.dead_name):
            await self.channel.queue_delete(name)


def bodies(messages: Iterable) -> list[dict]:
    return [json.loads(message.body) for message in messages]


async def count_charges(session_maker, order_id: uuid.UUID) -> int:
    async with session_maker() as session:
        result = await session.execute(
            select(func.count()).select_from(Charge).where(Charge.order_id == order_id)
        )
        return result.scalar_one()


async def count_refunds_of_order(session_maker, order_id: uuid.UUID) -> int:
    async with session_maker() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Refund)
            .join(Charge, Refund.charge_id == Charge.id)
            .where(Charge.order_id == order_id)
        )
        return result.scalar_one()
