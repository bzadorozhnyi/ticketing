"""Every branch of `OrderWorkflow` against Temporal's test server, with time skipped:
the 600 s before giving up pass at once. The activities are fakes that play payment by
signalling its answer, as `PaymentReplyConsumer` would.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from core.settings import TemporalSettings
from core.temporal import Heartbeats, TemporalClient
from orders.dto.messages import ChargeStatus, RefundStatus
from orders.dto.workflow import (
    Finish,
    OrderInput,
    PaymentRequest,
    Reservation,
    SagaTimings,
)
from orders.workflows.order_workflow import OrderWorkflow

pytestmark = pytest.mark.anyio

QUEUE = "orders-test"
TIMINGS = SagaTimings(
    reply_wait_seconds=30,
    max_reply_wait_seconds=120,
    give_up_after_seconds=600,
    retry_base_delay_seconds=1,
    max_retry_delay_seconds=30,
)


@pytest.fixture(scope="module")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        yield env


class World:
    """seat-hold, catalog, payment and the order row, as the activities see them."""

    def __init__(
        self,
        client: Client,
        *,
        reservation: Reservation | None = None,
        charge: ChargeStatus | None = ChargeStatus.SUCCEEDED,
        late_charge: ChargeStatus | None = None,
        confirms: list[bool | Exception] | None = None,
        confirm_seconds: float = 0,
        answer_refund: bool = True,
    ):
        self.client = client
        self.reservation = reservation or Reservation(
            amount_minor=150000, currency="UAH"
        )
        self.charge = charge
        self.late_charge = late_charge
        self.confirms = confirms or [True]
        self.confirm_seconds = confirm_seconds
        self.answer_refund = answer_refund
        self.calls: list[str] = []
        self.refund_reasons: list[str | None] = []
        self.finished: Finish | None = None

    @property
    def finish(self) -> Finish:
        assert self.finished is not None, "the order was not finished"
        return self.finished

    def activities(self) -> list:
        @activity.defn(name="reserve")
        async def reserve(order: OrderInput) -> Reservation:
            self.calls.append("reserve")
            return self.reservation

        @activity.defn(name="send_charge")
        async def send_charge(request: PaymentRequest) -> None:
            self.calls.append("send_charge")
            for status in (self.charge, self.late_charge):
                if status is not None:
                    await self._signal(request, OrderWorkflow.charge_replied, status)

        @activity.defn(name="send_refund")
        async def send_refund(request: PaymentRequest) -> None:
            self.calls.append("send_refund")
            self.refund_reasons.append(request.refund_reason)
            if self.answer_refund:
                await self._signal(
                    request, OrderWorkflow.refund_replied, RefundStatus.REFUNDED
                )

        @activity.defn(name="confirm")
        async def confirm(order: OrderInput) -> bool:
            self.calls.append("confirm")
            await asyncio.sleep(self.confirm_seconds)
            outcome = (
                self.confirms.pop(0) if len(self.confirms) > 1 else self.confirms[0]
            )
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        @activity.defn(name="finish")
        async def finish(finish: Finish) -> None:
            self.calls.append("finish")
            self.finished = finish

        return [reserve, send_charge, send_refund, confirm, finish]

    async def _signal(self, request: PaymentRequest, signal, status) -> None:
        handle = self.client.get_workflow_handle(str(request.order_id))
        await handle.signal(signal, status)


async def new_order(env: WorkflowEnvironment) -> OrderInput:
    """Created now on the test server's clock, which skips ahead between tests."""
    return OrderInput(
        order_id=uuid.uuid4(),
        hold_id=uuid.uuid4(),
        user_id="user-1",
        created_at=await env.get_current_time(),
        timings=TIMINGS,
    )


async def run(
    env: WorkflowEnvironment, world: World, order: OrderInput, interceptors=()
) -> str:
    async with Worker(
        env.client,
        task_queue=QUEUE,
        workflows=[OrderWorkflow],
        activities=world.activities(),
        interceptors=list(interceptors),
    ):
        # A workflow that never ends would hang the suite instead of failing it.
        async with asyncio.timeout(30):
            return await env.client.execute_workflow(
                OrderWorkflow.run, order, id=str(order.order_id), task_queue=QUEUE
            )


async def test_a_paid_order_is_confirmed(env):
    world = World(env.client)

    outcome = await run(env, world, await new_order(env))

    assert outcome == "confirmed"
    assert world.calls == ["reserve", "send_charge", "confirm", "finish"]
    assert world.finish.outcome == "confirmed"


async def test_a_reason_found_before_the_charge_fails_the_order_without_one(env):
    world = World(env.client, reservation=Reservation(failure_reason="hold_expired"))

    outcome = await run(env, world, await new_order(env))

    assert outcome == "failed"
    assert world.calls == ["reserve", "finish"]
    assert world.finish.failure_reason == "hold_expired"


async def test_a_declined_card_fails_the_order_and_keeps_the_hold(env):
    world = World(env.client, charge=ChargeStatus.DECLINED)

    outcome = await run(env, world, await new_order(env))

    assert outcome == "failed"
    assert world.calls == ["reserve", "send_charge", "finish"]
    assert world.finish.failure_reason == "card_declined"


async def test_no_answer_to_the_charge_sends_it_again_then_refunds_at_the_deadline(env):
    """Waits of 30, 60, 120, 120... s fit in 600 s; then a refund covers both cases,
    money taken or not."""
    world = World(env.client, charge=None)

    outcome = await run(env, world, await new_order(env))

    assert outcome == "refunded"
    assert world.calls.count("send_charge") >= 5
    assert "confirm" not in world.calls
    assert world.refund_reasons == ["charge_timed_out"]


async def test_a_hold_lost_after_the_charge_gives_the_money_back(env):
    world = World(env.client, confirms=[False])

    outcome = await run(env, world, await new_order(env))

    assert outcome == "refunded"
    assert world.refund_reasons == ["refunded_hold_lost"]


async def test_after_the_charge_confirm_is_tried_until_it_answers(env):
    """The seat may be confirmed already: a refund then would give it away for free.
    25 failures take longer than the 600 s the saga waits before the charge."""
    errors: list[bool | Exception] = [RuntimeError("seat-hold timed out")] * 25
    world = World(env.client, confirms=[*errors, True])

    outcome = await run(env, world, await new_order(env))

    assert outcome == "confirmed"
    assert world.calls.count("confirm") == 26


async def test_a_refund_without_an_answer_is_sent_again_and_never_given_up(env):
    world = World(env.client, confirms=[False], answer_refund=False)
    order = await new_order(env)

    async with Worker(
        env.client,
        task_queue=QUEUE,
        workflows=[OrderWorkflow],
        activities=world.activities(),
    ):
        handle = await env.client.start_workflow(
            OrderWorkflow.run, order, id=str(order.order_id), task_queue=QUEUE
        )
        await env.sleep(3600)
        assert world.refund_reasons.count("refunded_hold_lost") >= 5
        assert world.finished is None
        await handle.signal(OrderWorkflow.refund_replied, RefundStatus.REFUNDED)
        assert await handle.result() == "refunded"


async def test_a_second_answer_to_the_charge_changes_nothing(env):
    world = World(env.client, late_charge=ChargeStatus.DECLINED)

    outcome = await run(env, world, await new_order(env))

    assert outcome == "confirmed"


async def test_a_reply_after_the_end_changes_nothing(env):
    world = World(env.client)
    order = await new_order(env)

    outcome = await run(env, world, order)
    handle = env.client.get_workflow_handle(str(order.order_id))
    temporal = TemporalClient(settings=TemporalSettings(TASK_QUEUE=QUEUE))
    temporal._client = env.client

    assert outcome == "confirmed"
    assert not await temporal.signal(
        workflow_id=str(order.order_id),
        signal=OrderWorkflow.charge_replied,
        arg=ChargeStatus.DECLINED,
    )
    assert await handle.result() == "confirmed"


async def test_a_finished_order_is_never_run_again(env):
    """Temporal would start a new run of a finished id by default; the second run would
    find the hold confirmed and fail a paid order."""
    world = World(env.client)
    order = await new_order(env)
    temporal = TemporalClient(settings=TemporalSettings(TASK_QUEUE=QUEUE))
    temporal._client = env.client
    await run(env, world, order)

    started = await temporal.start(
        workflow=OrderWorkflow.run, arg=order, workflow_id=str(order.order_id)
    )

    assert started is False
    assert world.calls.count("reserve") == 1


async def test_a_slow_live_activity_keeps_beating_and_is_not_repeated(env):
    """3 s of a live call against a 2 s heartbeat timeout: the beats keep it alive, so a
    short timeout can find a dead worker without repeating slow calls."""
    world = World(env.client, confirm_seconds=3)

    outcome = await run(env, world, await new_order(env), interceptors=[Heartbeats()])

    assert outcome == "confirmed"
    assert world.calls.count("confirm") == 1
