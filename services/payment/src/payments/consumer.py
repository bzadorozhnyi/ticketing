import asyncio
from collections.abc import Callable
from typing import Literal

from loguru import logger
from opentelemetry.trace import SpanKind
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.exceptions import ConflictException
from core.rabbitmq import Delivery
from core.settings import FaultSettings, RabbitMQSettings
from core.telemetry import commands, continue_trace, current_traceparent
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
from payments.services.pending_charge import PendingChargeFinisher
from payments.services.provider import MockProvider, kill_process
from payments.use_cases.charge import ChargeUseCase
from payments.use_cases.refund import RefundUseCase

# The `outcome` of `payment.commands`; a handled command also has its `result`.
CommandOutcome = Literal["handled", "retry", "dead_letter", "malformed"]


def count_command(
    *,
    outcome: CommandOutcome,
    command_type: str | None = None,
    result: str | None = None,
) -> None:
    attributes = {"outcome": outcome}
    if command_type is not None:
        attributes["command.type"] = command_type
    if result is not None:
        attributes["result"] = result
    commands.add(1, attributes)


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
        """A consumer span, a child of the relay's span that published the command;
        the reply carries this span's `traceparent`."""
        name = "process payment command"
        with continue_trace(delivery.traceparent, name=name, kind=SpanKind.CONSUMER):
            await self._handle(delivery=delivery)

    async def _handle(self, *, delivery: Delivery) -> None:
        try:
            command = COMMAND_ADAPTER.validate_json(delivery.body)
        except ValidationError as exc:
            logger.bind(error=repr(exc)).error("Malformed command")
            await delivery.dead_letter()
            count_command(outcome="malformed")
            return

        try:
            async with asyncio.timeout(self.settings.HANDLER_TIMEOUT_SECONDS):
                result = await self._process(
                    command=command, first_delivery=not delivery.redelivered
                )
                await self.replies.send(
                    result=result, traceparent=current_traceparent()
                )
        except ConflictException as exc:
            logger.bind(error=repr(exc)).error("Command can never succeed")
            await delivery.dead_letter()
            count_command(outcome="dead_letter", command_type=command.type)
        except Exception as exc:
            logger.bind(error=repr(exc)).warning("Command failed, will retry")
            await self._retry_later(delivery=delivery)
            count_command(outcome="retry", command_type=command.type)
        else:
            await delivery.ack()
            logger.bind(order_id=str(command.order_id)).info(
                f"Command {command.type} handled"
            )
            count_command(
                outcome="handled", command_type=command.type, result=result.status
            )

    async def _process(
        self, *, command: Command, first_delivery: bool
    ) -> ChargeResult | RefundResult:
        async with self.session_maker() as session:
            charges = ChargeRepository(session=session)
            provider = MockProvider(
                session_maker=self.session_maker,
                faults=self.faults,
                crash=self.crash,
                armed=first_delivery,
            )
            finisher = PendingChargeFinisher(
                session=session, charge_repository=charges, provider=provider
            )
            if isinstance(command, ChargeCommand):
                use_case = ChargeUseCase(
                    session=session,
                    charge_repository=charges,
                    finisher=finisher,
                    provider=provider,
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
                session=session,
                charge_repository=charges,
                refund_repository=refunds,
                finisher=finisher,
                provider=provider,
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
