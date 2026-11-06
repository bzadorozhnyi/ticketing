import asyncio
import secrets
from collections.abc import Callable

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.exceptions import ConflictException
from core.logging import trace_id_var
from core.middlewares.trace_context import parse_trace_id
from core.rabbitmq import Delivery
from core.settings import FaultSettings, RabbitMQSettings
from payments.dto.messages import (
    COMMAND_ADAPTER,
    ChargeCommand,
    ChargeResult,
    Command,
    RefundResult,
)
from payments.dto.payment import ChargeRequest
from payments.queues.replies import PaymentRepliesQueue
from payments.repositories.charge import ChargeRepository
from payments.repositories.refund import RefundRepository
from payments.services.provider import MockProvider, kill_process
from payments.use_cases.charge import ChargeUseCase
from payments.use_cases.refund import RefundUseCase


class CommandConsumer:
    """Acknowledges last: process, commit, send the reply, then ack. A crash at any
    point redelivers the command, and the use cases make the repeat safe.
    """

    def __init__(
        self,
        *,
        replies: PaymentRepliesQueue,
        session_maker: async_sessionmaker[AsyncSession],
        settings: RabbitMQSettings,
        faults: FaultSettings,
        crash: Callable[[], None] = kill_process,
    ):
        self.replies = replies
        self.session_maker = session_maker
        self.settings = settings
        self.faults = faults
        self.crash = crash

    async def handle(self, delivery: Delivery) -> None:
        traceparent = delivery.traceparent
        trace_id_var.set(
            parse_trace_id(traceparent=traceparent) or secrets.token_hex(16)
        )
        try:
            command = COMMAND_ADAPTER.validate_json(delivery.body)
        except ValidationError as exc:
            logger.bind(error=repr(exc)).error("Malformed command")
            await delivery.dead_letter()
            return

        try:
            async with asyncio.timeout(self.settings.HANDLER_TIMEOUT_SECONDS):
                result = await self._process(
                    command=command, first_delivery=not delivery.redelivered
                )
                await self.replies.send(result=result, traceparent=traceparent)
        except ConflictException as exc:
            logger.bind(error=repr(exc)).error("Command can never succeed")
            await delivery.dead_letter()
        except Exception as exc:
            logger.bind(error=repr(exc)).warning("Command failed, will retry")
            await self._retry_later(delivery=delivery)
        else:
            await delivery.ack()
            logger.bind(order_id=str(command.order_id)).info(
                f"Command {command.type} handled"
            )

    async def _process(
        self, *, command: Command, first_delivery: bool
    ) -> ChargeResult | RefundResult:
        async with self.session_maker() as session:
            charges = ChargeRepository(session=session)
            if isinstance(command, ChargeCommand):
                provider = MockProvider(
                    faults=self.faults, crash=self.crash, armed=first_delivery
                )
                use_case = ChargeUseCase(
                    session=session, charge_repository=charges, provider=provider
                )
                charge = await use_case.execute(
                    request=ChargeRequest(
                        order_id=command.order_id,
                        amount_minor=command.amount_minor,
                        currency=command.currency,
                        payment_token=command.payment_token,
                    )
                )
                return ChargeResult(
                    order_id=charge.order_id,
                    charge_id=charge.charge_id,
                    status=charge.status,
                )

            refunds = RefundRepository(session=session)
            status = await RefundUseCase(
                session=session, charge_repository=charges, refund_repository=refunds
            ).execute(
                order_id=command.order_id,
                amount_minor=command.amount_minor,
                currency=command.currency,
            )
            return RefundResult(order_id=command.order_id, status=status)

    async def _retry_later(self, *, delivery: Delivery) -> None:
        """Without a pause an outage of a few seconds would burn the delivery limit
        in milliseconds and send every command to the dead letter queue.
        """
        delay = self.settings.RETRY_BASE_DELAY_SECONDS * 2**delivery.attempts
        await asyncio.sleep(min(delay, self.settings.MAX_RETRY_DELAY_SECONDS))
        await delivery.retry()
