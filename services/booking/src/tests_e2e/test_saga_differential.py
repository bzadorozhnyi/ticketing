"""One generated scenario of the outside world through both saga engines: the saga
(`SagaRunner`, the use cases, `PaymentReplyConsumer`) and `OrderWorkflow` on Temporal's
test server. Both must end as a small model of the rules says, and so alike.

A scenario says what seat-hold and catalog answer, what payment replies to the charge
(noise first, a definite answer or none, a late contradicting one), how often confirm
fails before it answers, and whether the refund's answer comes twice. Final results are
compared, not the number of repeated commands: the saga's polls and the workflow's
timers may repeat a charge a different number of times before the deadline.
"""

import json
import uuid
from typing import Literal

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from core.db import async_session
from core.settings import SagaSettings
from orders.dto.messages import ChargeStatus, RefundStatus
from orders.dto.workflow import (
    Finish,
    OrderInput,
    PaymentRequest,
    Reservation,
    SagaTimings,
)
from orders.models.order import FailureReason, OrderStatus, SagaStep
from orders.payment_reply_consumer import PaymentReplyConsumer
from orders.workflows.order_workflow import OrderWorkflow
from tests_e2e.helpers import (
    Reply,
    Upstreams,
    create_order,
    load,
    make_due,
    make_runner,
    outbox,
)
from tests_e2e.test_saga_finish import RecordedDelivery, charge_result, refund_result
from tests_e2e.test_saga_recovery import make_overdue

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("empty_tables")]

QUEUE = "orders-differential"
MAX_STEPS = 30


class Scenario(BaseModel):
    hold: Literal["ok", "missing", "expired"]
    price_found: bool
    # Payment's replies to the first charge, in order; a later charge gets none.
    charge_replies: list[ChargeStatus]
    confirm_errors: int
    confirmed: bool
    refund_reply_twice: bool


class Outcome(BaseModel):
    status: str
    failure_reason: str | None
    refunded: bool


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    """Weighted towards the paths that go far: a hold, a price and a paid charge."""
    answers = st.sampled_from(
        [ChargeStatus.SUCCEEDED, ChargeStatus.SUCCEEDED, ChargeStatus.DECLINED, None]
    )
    noise = [ChargeStatus.CANCELLED] if draw(st.booleans()) else []
    answer = draw(answers)
    late = draw(answers) if answer else None
    return Scenario(
        hold=draw(st.sampled_from(["ok"] * 8 + ["missing", "expired"])),
        price_found=draw(st.sampled_from([True] * 9 + [False])),
        charge_replies=[*noise, *(r for r in (answer, late) if r)],
        confirm_errors=draw(st.integers(0, 3)),
        confirmed=draw(st.booleans()),
        refund_reply_twice=draw(st.booleans()),
    )


def expected(scenario: Scenario) -> Outcome:
    """The rules of the purchase, in the order the saga meets them."""
    if scenario.hold == "missing":
        return Outcome(status="failed", failure_reason="hold_not_found", refunded=False)
    if not scenario.price_found:
        return Outcome(
            status="failed", failure_reason="price_not_found", refunded=False
        )
    if scenario.hold == "expired":
        return Outcome(status="failed", failure_reason="hold_expired", refunded=False)
    definite = [r for r in scenario.charge_replies if r != ChargeStatus.CANCELLED]
    if not definite:
        return Outcome(
            status="failed", failure_reason="charge_timed_out", refunded=True
        )
    if definite[0] == ChargeStatus.DECLINED:
        return Outcome(status="failed", failure_reason="card_declined", refunded=False)
    if scenario.confirmed:
        return Outcome(status="confirmed", failure_reason=None, refunded=False)
    return Outcome(status="failed", failure_reason="refunded_hold_lost", refunded=True)


# The saga


async def deliver(content: bytes) -> None:
    delivery = RecordedDelivery(content=content)
    await PaymentReplyConsumer(
        session_maker=async_session, settings=SagaSettings()
    ).handle(delivery)
    assert delivery.settled == ["ack"]


async def commands_of(order_id: uuid.UUID) -> list[str]:
    bodies = [json.loads(message.body) for message in await outbox()]
    return [body["type"] for body in bodies if body["order_id"] == str(order_id)]


def set_upstreams(upstreams: Upstreams, scenario: Scenario) -> None:
    upstreams.calls.clear()
    upstreams.hold = Reply(status=404 if scenario.hold == "missing" else 200)
    upstreams.extend = Reply(status=409 if scenario.hold == "expired" else 200)
    upstreams.price = Reply(status=200 if scenario.price_found else 404)


async def run_saga(scenario: Scenario, upstreams: Upstreams, http) -> Outcome:
    """Steps of `SagaRunner`, payment played by answering each new command, and time
    moved past the deadline when payment stays silent. Commands are counted by type:
    rows of the outbox come back in no particular order."""
    set_upstreams(upstreams, scenario)
    order_id = await create_order()
    runner = make_runner(http)
    charges_answered = refunds_answered = 0
    for _ in range(MAX_STEPS):
        order = await load(order_id)
        if order.status != OrderStatus.PENDING:
            break
        errors_left = scenario.confirm_errors - upstreams.called("confirm")
        final = Reply(status=200 if scenario.confirmed else 409)
        upstreams.confirm = Reply(status=503) if errors_left > 0 else final
        await make_due(order_id)
        await runner.process_next()
        commands = await commands_of(order_id)
        if commands.count("charge") and not charges_answered:
            for reply in scenario.charge_replies:
                await deliver(charge_result(order_id, reply))
            charges_answered = 1
        for _ in range(commands.count("refund") - refunds_answered):
            for _ in range(2 if scenario.refund_reply_twice else 1):
                await deliver(refund_result(order_id, RefundStatus.REFUNDED))
        refunds_answered = commands.count("refund")
        order = await load(order_id)
        if order.saga_step == SagaStep.AWAITING_CHARGE:
            await make_overdue(order_id)
    order = await load(order_id)
    return Outcome(
        status=order.status,
        failure_reason=order.failure_reason,
        refunded="refund" in await commands_of(order_id),
    )


# The workflow


class World:
    """seat-hold, catalog and payment for the workflow, played from the scenario."""

    def __init__(self, env: WorkflowEnvironment, scenario: Scenario):
        self.env = env
        self.scenario = scenario
        self.charges = 0
        self.confirms = 0
        self.refund_reason: str | None = None
        self.finished: Finish | None = None

    @activity.defn(name="reserve")
    async def reserve(self, order: OrderInput) -> Reservation:
        if self.scenario.hold == "missing":
            return Reservation(failure_reason=FailureReason.HOLD_NOT_FOUND)
        if not self.scenario.price_found:
            return Reservation(failure_reason=FailureReason.PRICE_NOT_FOUND)
        if self.scenario.hold == "expired":
            return Reservation(failure_reason=FailureReason.HOLD_EXPIRED)
        return Reservation(amount_minor=150_000, currency="UAH")

    @activity.defn(name="send_charge")
    async def send_charge(self, request: PaymentRequest) -> None:
        self.charges += 1
        if self.charges == 1:
            for reply in self.scenario.charge_replies:
                await self._signal(request, OrderWorkflow.charge_replied, reply)

    @activity.defn(name="send_refund")
    async def send_refund(self, request: PaymentRequest) -> None:
        self.refund_reason = request.refund_reason
        for _ in range(2 if self.scenario.refund_reply_twice else 1):
            await self._signal(
                request, OrderWorkflow.refund_replied, RefundStatus.REFUNDED
            )

    @activity.defn(name="confirm")
    async def confirm(self, order: OrderInput) -> bool:
        self.confirms += 1
        if self.confirms <= self.scenario.confirm_errors:
            raise RuntimeError("seat-hold answered 503")
        return self.scenario.confirmed

    @activity.defn(name="finish")
    async def finish(self, finish: Finish) -> None:
        self.finished = finish

    def activities(self) -> list:
        return [
            self.reserve,
            self.send_charge,
            self.send_refund,
            self.confirm,
            self.finish,
        ]

    async def _signal(self, request: PaymentRequest, signal, status) -> None:
        handle = self.env.client.get_workflow_handle(str(request.order_id))
        await handle.signal(signal, status)


async def run_workflow(scenario: Scenario, env: WorkflowEnvironment) -> Outcome:
    world = World(env, scenario)
    saga = SagaSettings()
    order = OrderInput(
        order_id=uuid.uuid4(),
        hold_id=uuid.uuid4(),
        user_id="user-1",
        created_at=await env.get_current_time(),
        timings=SagaTimings(
            reply_wait_seconds=saga.REPLY_WAIT_SECONDS,
            max_reply_wait_seconds=saga.MAX_REPLY_WAIT_SECONDS,
            give_up_after_seconds=saga.GIVE_UP_AFTER_SECONDS,
            retry_base_delay_seconds=saga.RETRY_BASE_DELAY_SECONDS,
            max_retry_delay_seconds=saga.MAX_RETRY_DELAY_SECONDS,
        ),
    )
    async with Worker(
        env.client,
        task_queue=QUEUE,
        workflows=[OrderWorkflow],
        activities=world.activities(),
    ):
        await env.client.execute_workflow(
            OrderWorkflow.run, order, id=str(order.order_id), task_queue=QUEUE
        )
    finished = world.finished
    assert finished is not None
    refunded = world.refund_reason is not None
    return Outcome(
        status="confirmed" if finished.outcome == "confirmed" else "failed",
        # A refunded order fails with the reason its refund was sent with.
        failure_reason=world.refund_reason if refunded else finished.failure_reason,
        refunded=refunded,
    )


# Both


@pytest.fixture(scope="module")
async def env():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        yield env


@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(scenario=scenarios())
async def test_both_engines_end_as_the_rules_say(
    scenario: Scenario, upstreams, http, env
):
    want = expected(scenario)

    saga = await run_saga(scenario, upstreams, http)
    workflow = await run_workflow(scenario, env)

    event(f"{want.status} {want.failure_reason}")
    assert saga == want, "the saga"
    assert workflow == want, "the workflow"
