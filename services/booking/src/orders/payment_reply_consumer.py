import asyncio

from loguru import logger
from opentelemetry.trace import SpanKind
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.backoff import backoff_seconds
from core.faults import crash_point
from core.rabbitmq import Delivery
from core.settings import SagaSettings
from core.telemetry import continue_trace, mark_span_failed, record_finished_order
from core.temporal import TemporalClient
from orders.dto.messages import REPLY_ADAPTER, ChargeResult, Reply
from orders.models.order import SagaEngine
from orders.repositories.order import OrderRepository
from orders.use_cases.apply_charge_result import ApplyChargeResultUseCase
from orders.use_cases.apply_refund_result import ApplyRefundResultUseCase
from orders.workflows.order_workflow import OrderWorkflow


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
        temporal: TemporalClient | None = None,
    ):
        self.session_maker = session_maker
        self.settings = settings
        self.temporal = temporal

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
            mark_span_failed(exc)
            await delivery.dead_letter()
            return

        try:
            async with asyncio.timeout(self.settings.REPLY_TIMEOUT_SECONDS):
                await self._process(reply=reply)
        except Exception as exc:
            logger.bind(error=repr(exc)).warning("Reply failed, will retry")
            mark_span_failed(exc)
            await self._retry_later(delivery=delivery)
        else:
            crash_point("after_reply_commit")
            await delivery.ack()

    async def _process(self, *, reply: Reply) -> None:
        async with self.session_maker() as session:
            repository = OrderRepository(session=session)
            if (
                await repository.engine_of(order_id=reply.order_id)
                == SagaEngine.TEMPORAL
            ):
                await session.rollback()
                await self._signal(reply=reply)
                return
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
                created_at=finished.created_at,
                status=finished.status,
                failure_reason=finished.failure_reason,
            )

    async def _signal(self, *, reply: Reply) -> None:
        """A Temporal order's state is in its workflow: the reply goes there."""
        if self.temporal is None:
            raise RuntimeError("A reply for a Temporal order, and no Temporal client")
        signal = (
            OrderWorkflow.charge_replied
            if isinstance(reply, ChargeResult)
            else OrderWorkflow.refund_replied
        )
        delivered = await self.temporal.signal(
            workflow_id=str(reply.order_id), signal=signal, arg=reply.status
        )
        if not delivered:
            logger.bind(order_id=str(reply.order_id)).info(
                "Reply ignored, the workflow has finished"
            )

    async def _retry_later(self, *, delivery: Delivery) -> None:
        await asyncio.sleep(
            backoff_seconds(
                base=self.settings.RETRY_BASE_DELAY_SECONDS,
                attempt=delivery.attempts,
                limit=self.settings.MAX_RETRY_DELAY_SECONDS,
            )
        )
        await delivery.retry()
