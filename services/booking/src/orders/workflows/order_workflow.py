from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from orders.dto.messages import ChargeStatus, RefundStatus
    from orders.dto.workflow import Finish, OrderInput, PaymentRequest, Reservation
    from orders.models.order import FailureReason
    from orders.workflows.activities import OrderActivities

# How long one attempt may run before the server gives it to another worker: a little
# above the longest legitimate run, since a dead worker is noticed only by this timeout.
# `reserve` makes three calls of at most UPSTREAMS__TIMEOUT_SECONDS (3 s) each,
# `confirm` one; the others write to the database and publish.
ACTIVITY_TIMEOUTS = {
    OrderActivities.reserve: timedelta(seconds=15),
    OrderActivities.confirm: timedelta(seconds=5),
}
ACTIVITY_TIMEOUT = timedelta(seconds=10)
# Activities heartbeat every 0.5 s (core/temporal.py): a missed beat is a dead worker.
HEARTBEAT_TIMEOUT = timedelta(seconds=2)


@workflow.defn
class OrderWorkflow:
    """booking's purchase saga as one function (ADR 0018): the steps and rules of
    `SagaRunner`, with Temporal keeping the state. Payment answers through signals,
    which may arrive before the workflow waits for them.

    Before the charge the saga gives up after `give_up_after_seconds`. Once the money
    may be taken only a definite answer ends it: confirm and refund never give up.
    """

    def __init__(self) -> None:
        self.charge: ChargeStatus | None = None
        self.refunded = False

    @workflow.signal
    def charge_replied(self, status: ChargeStatus) -> None:
        """The first answer counts; a repeat or a late one changes nothing."""
        if status == ChargeStatus.CANCELLED:
            workflow.logger.error("Charge was cancelled while the saga waits for it")
        elif self.charge is None:
            self.charge = status

    @workflow.signal
    def refund_replied(self, status: RefundStatus) -> None:
        """Either status ends the order: the money is back, or it was never taken."""
        self.refunded = True

    @workflow.run
    async def run(self, order: OrderInput) -> str:
        deadline = order.created_at + timedelta(
            seconds=order.timings.give_up_after_seconds
        )
        reservation = await self._reserve(order=order, deadline=deadline)
        if reservation is None:
            return await self._finish(order, "failed", FailureReason.SAGA_TIMED_OUT)
        if reservation.failure_reason:
            return await self._finish(order, "failed", reservation.failure_reason)

        payment = PaymentRequest(
            order_id=order.order_id,
            amount_minor=reservation.amount_minor,
            currency=reservation.currency,
        )
        charge = await self._charge(order=order, payment=payment, deadline=deadline)
        if charge is None:
            return await self._refund(order, payment, FailureReason.CHARGE_TIMED_OUT)
        if charge == ChargeStatus.DECLINED:
            return await self._finish(order, "failed", FailureReason.CARD_DECLINED)

        if await self._run(OrderActivities.confirm, order, order=order):
            return await self._finish(order, "confirmed", None)
        return await self._refund(order, payment, FailureReason.REFUNDED_HOLD_LOST)

    async def _reserve(
        self, *, order: OrderInput, deadline: datetime
    ) -> Reservation | None:
        """None after the deadline: nothing was charged, so the order just fails."""
        if workflow.now() >= deadline:
            return None
        try:
            return await self._run(
                OrderActivities.reserve, order, order=order, until=deadline
            )
        except ActivityError:
            return None

    async def _charge(
        self, *, order: OrderInput, payment: PaymentRequest, deadline: datetime
    ) -> ChargeStatus | None:
        """Sends the charge and waits for its answer, sending it again after each wait
        (payment answers a repeat with the first result). None after the deadline."""
        attempt = 0
        while self.charge is None:
            if workflow.now() >= deadline:
                return None
            try:
                await self._run(
                    OrderActivities.send_charge, payment, order=order, until=deadline
                )
                left = deadline - workflow.now()
                if left <= timedelta(0):
                    continue
                await workflow.wait_condition(
                    lambda: self.charge is not None,
                    timeout=min(self._reply_wait(order, attempt), left),
                )
            except ActivityError, TimeoutError:
                attempt += 1
        return self.charge

    async def _refund(
        self, order: OrderInput, payment: PaymentRequest, reason: FailureReason
    ) -> str:
        """The customer's money: sent again until payment answers, never given up."""
        refund = payment.model_copy(update={"refund_reason": reason})
        attempt = 0
        while not self.refunded:
            await self._run(OrderActivities.send_refund, refund, order=order)
            try:
                await workflow.wait_condition(
                    lambda: self.refunded, timeout=self._reply_wait(order, attempt)
                )
            except TimeoutError:
                attempt += 1
                workflow.logger.warning("Refund has no answer, sending it again")
        return await self._finish(order, "refunded", None)

    async def _finish(self, order: OrderInput, outcome: Any, reason: str | None) -> str:
        finish = Finish(order_id=order.order_id, outcome=outcome, failure_reason=reason)
        await self._run(OrderActivities.finish, finish, order=order)
        return outcome

    async def _run(
        self,
        activity: Any,
        arg: Any,
        *,
        order: OrderInput,
        until: datetime | None = None,
    ) -> Any:
        """Retried with backoff; for ever, or until `until`."""
        timings = order.timings
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=timings.retry_base_delay_seconds),
            maximum_interval=timedelta(seconds=timings.max_retry_delay_seconds),
        )
        return await workflow.execute_activity_method(
            activity,
            arg,
            start_to_close_timeout=ACTIVITY_TIMEOUTS.get(activity, ACTIVITY_TIMEOUT),
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
            schedule_to_close_timeout=until - workflow.now() if until else None,
            retry_policy=retry,
        )

    @staticmethod
    def _reply_wait(order: OrderInput, attempt: int) -> timedelta:
        """As `RecoverOrderUseCase` waits: doubling, up to a maximum."""
        timings = order.timings
        seconds = min(
            timings.reply_wait_seconds * 2**attempt, timings.max_reply_wait_seconds
        )
        return timedelta(seconds=seconds)
