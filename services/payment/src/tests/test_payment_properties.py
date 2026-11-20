"""Charges and refunds of a few orders in any order Hypothesis makes up: repeats, a
refund before its charge, another amount for the same order, a declined card, and the
provider dying before or after our commit. Money is counted by the provider's ledger,
the only record of what was really taken, after every step.

Each command runs in a new session, as a new process would after a crash.
"""

import uuid
from typing import Literal

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

from core.exceptions import ConflictException
from core.settings import FaultSettings
from payments.dto.payment import ChargeRequest, RefundStatus
from payments.models.charge import ChargeStatus
from payments.repositories.charge import ChargeRepository
from payments.repositories.refund import RefundRepository
from payments.services.pending_charge import PendingChargeFinisher
from payments.services.provider import (
    TOKEN_CRASH_AFTER_COMMIT,
    TOKEN_CRASH_BEFORE_COMMIT,
    TOKEN_DECLINED,
    MockProvider,
)
from payments.use_cases.charge import ChargeUseCase
from payments.use_cases.refund import RefundUseCase
from tests.helpers.ledger import money_held
from tests.helpers.provider import provider_sessions

pytestmark = pytest.mark.anyio

AMOUNT = 10_000
TOKENS = ["tok_ok", TOKEN_DECLINED, TOKEN_CRASH_BEFORE_COMMIT, TOKEN_CRASH_AFTER_COMMIT]


class SimulatedCrash(Exception):
    pass


def raise_crash() -> None:
    raise SimulatedCrash


class Charge(BaseModel):
    kind: Literal["charge"] = "charge"
    order: int
    token: str
    # A redelivery runs with the provider's faults off, as the consumer does.
    armed: bool
    other_amount: bool


class Refund(BaseModel):
    kind: Literal["refund"] = "refund"
    order: int
    armed: bool


Command = Charge | Refund

commands = st.lists(
    st.one_of(
        st.builds(
            Charge,
            order=st.integers(0, 1),
            token=st.sampled_from(TOKENS),
            armed=st.booleans(),
            # Rare: a conflict changes nothing, and half the commands were spent on it.
            other_amount=st.integers(0, 4).map(lambda n: n == 0),
        ),
        st.builds(Refund, order=st.integers(0, 1), armed=st.booleans()),
    ),
    min_size=1,
    max_size=16,
)


class OrderModel:
    """What we know of one order from the answers it got."""

    def __init__(self):
        self.touched = False
        # The token of the first charge to reach the database, or "refund" if a refund
        # came first: it decides every later answer.
        self.first: str | None = None
        self.charge_answer: str | None = None
        self.refunded = False

    def expected_charge_answers(self) -> set[str]:
        if self.charge_answer is not None:
            return {self.charge_answer}
        if self.first == "refund":
            return {ChargeStatus.CANCELLED}
        if self.first == TOKEN_DECLINED:
            return {ChargeStatus.DECLINED}
        return {ChargeStatus.SUCCEEDED}


def provider(session, *, armed: bool) -> MockProvider:
    return MockProvider(
        session_maker=provider_sessions(session),
        faults=FaultSettings(),
        crash=raise_crash,
        armed=armed,
    )


async def run_charge(session, order_id: uuid.UUID, command: Charge) -> str:
    charges = ChargeRepository(session=session)
    mock = provider(session, armed=command.armed)
    use_case = ChargeUseCase(
        session=session,
        charge_repository=charges,
        finisher=PendingChargeFinisher(
            session=session, charge_repository=charges, provider=mock
        ),
        provider=mock,
    )
    request = ChargeRequest(
        order_id=order_id,
        amount_minor=AMOUNT + 1 if command.other_amount else AMOUNT,
        currency="UAH",
        payment_token=command.token,
    )
    return (await use_case.execute(request=request)).status


async def run_refund(session, order_id: uuid.UUID, command: Refund) -> RefundStatus:
    charges = ChargeRepository(session=session)
    mock = provider(session, armed=command.armed)
    use_case = RefundUseCase(
        session=session,
        charge_repository=charges,
        refund_repository=RefundRepository(session=session),
        finisher=PendingChargeFinisher(
            session=session, charge_repository=charges, provider=mock
        ),
        provider=mock,
    )
    return await use_case.execute(
        order_id=order_id, amount_minor=AMOUNT, currency="UAH"
    )


@settings(
    deadline=None,
    # The examples share the test's transaction, each inside a savepoint of its own.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(commands=commands)
async def test_money_is_the_amount_or_nothing_whatever_the_order_of_commands(
    db_connection, session_factory, commands: list[Command]
):
    """Each example is rolled back at its end: a committed savepoint keeps its locks
    until the transaction ends, and those of 2,000 examples overflow Postgres's lock
    table ("out of shared memory")."""
    order_ids = [uuid.uuid4() for _ in range(3)]
    models = [OrderModel() for _ in range(3)]

    example = await db_connection.begin_nested()
    try:
        for command in commands:
            order_id, model = order_ids[command.order], models[command.order]
            async with session_factory(
                bind=db_connection, join_transaction_mode="create_savepoint"
            ) as session:
                await apply(session, order_id, model, command)
            async with session_factory(
                bind=db_connection, join_transaction_mode="create_savepoint"
            ) as session:
                await check(session, order_id, model)
    finally:
        await example.rollback()


async def apply(session, order_id: uuid.UUID, model: OrderModel, command: Command):
    if isinstance(command, Charge) and command.other_amount and model.touched:
        with pytest.raises(ConflictException):
            await run_charge(session, order_id, command)
        event("another amount refused")
        return
    if isinstance(command, Charge) and command.other_amount:
        # The first command of an order sets its amount: keep it the model's.
        command = command.model_copy(update={"other_amount": False})
    if model.first is None:
        model.first = command.token if isinstance(command, Charge) else "refund"
    model.touched = True
    try:
        if isinstance(command, Charge):
            answer = await run_charge(session, order_id, command)
            assert answer in model.expected_charge_answers()
            model.charge_answer = answer
            event(f"charge answered {answer}")
        else:
            await run_refund(session, order_id, command)
            model.refunded = True
            event("refund answered")
    except SimulatedCrash:
        event("provider crashed")


async def check(session, order_id: uuid.UUID, model: OrderModel):
    held = await money_held(session, order_id)
    assert held in (0, AMOUNT)
    if model.refunded:
        assert held == 0
    elif model.charge_answer == ChargeStatus.SUCCEEDED:
        assert held == AMOUNT
    elif model.charge_answer in (ChargeStatus.DECLINED, ChargeStatus.CANCELLED):
        assert held == 0
