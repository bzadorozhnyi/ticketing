import asyncio

from loguru import logger
from opentelemetry.trace import SpanKind
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.faults import crash_point
from core.rabbitmq import Delivery
from core.settings import SagaSettings
from core.telemetry import continue_trace, record_finished_order
from orders.dto.messages import REPLY_ADAPTER, ChargeResult, Reply
from orders.repositories.order import OrderRepository
from orders.use_cases.apply_charge_result import ApplyChargeResultUseCase
from orders.use_cases.apply_refund_result import ApplyRefundResultUseCase


class PaymentReplyConsumer:
    """Moves the saga forward when payment answers a command.

    Payment replies through a queue, so this is the only way a saga leaves
    `awaiting_charge` and `awaiting_refund`. It only records the answer in the order;
    SagaRunner sees the new step on its next pass and makes the HTTP calls.

    The acknowledgement comes last: a crash redelivers the reply, and the step check
    in the use cases makes the repeat harmless.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        settings: SagaSettings,
    ):
        self.session_maker = session_maker
        self.settings = settings

    async def handle(self, delivery: Delivery) -> None:
        """A consumer span, a child of payment's span that sent the reply."""
        name = "process payment reply"
        with continue_trace(delivery.traceparent, name=name, kind=SpanKind.CONSUMER):
            await self._handle(delivery=delivery)

    async def _handle(self, *, delivery: Delivery) -> None:
        try:
            reply = REPLY_ADAPTER.validate_json(delivery.body)
        except ValidationError as exc:
            logger.bind(error=repr(exc)).error("Malformed reply")
            await delivery.dead_letter()
            return

        try:
            async with asyncio.timeout(self.settings.REPLY_TIMEOUT_SECONDS):
                await self._process(reply=reply)
        except Exception as exc:
            logger.bind(error=repr(exc)).warning("Reply failed, will retry")
            await self._retry_later(delivery=delivery)
        else:
            crash_point("after_reply_commit")
            await delivery.ack()

    async def _process(self, *, reply: Reply) -> None:
        async with self.session_maker() as session:
            repository = OrderRepository(session=session)
            if isinstance(reply, ChargeResult):
                finished = await ApplyChargeResultUseCase(
                    session=session, order_repository=repository
                ).execute(result=reply)
            else:
                finished = await ApplyRefundResultUseCase(
                    session=session, order_repository=repository
                ).execute(result=reply)
        if finished:
            record_finished_order(
                created_at=finished.created_at, status=finished.status
            )

    async def _retry_later(self, *, delivery: Delivery) -> None:
        delay = self.settings.RETRY_BASE_DELAY_SECONDS * 2**delivery.attempts
        await asyncio.sleep(min(delay, self.settings.MAX_RETRY_DELAY_SECONDS))
        await delivery.retry()
