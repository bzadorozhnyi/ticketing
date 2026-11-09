import uuid

import pytest

from core.exceptions import ConflictException
from core.settings import FaultSettings
from payments.models.charge import ChargeStatus
from payments.services.pending_charge import charge_key
from payments.services.provider import TOKEN_DECLINED, MockProvider
from tests.helpers.ledger import money_held, operations
from tests.helpers.provider import provider_sessions

pytestmark = pytest.mark.anyio


def provider(session, faults=None) -> MockProvider:
    return MockProvider(
        session_maker=provider_sessions(session), faults=faults or FaultSettings()
    )


async def test_a_repeat_with_the_same_key_returns_the_first_answer(db_session):
    order_id = uuid.uuid4()
    key = charge_key(order_id=order_id)
    mock = provider(db_session)

    first = await mock.charge(
        idempotency_key=key, token=TOKEN_DECLINED, amount_minor=10_000, currency="UAH"
    )
    repeat = await mock.charge(
        idempotency_key=key, token="tok_ok", amount_minor=10_000, currency="UAH"
    )

    assert first == repeat == ChargeStatus.DECLINED
    assert await operations(db_session, order_id) == 1
    assert await money_held(db_session, order_id) == 0


async def test_a_random_decline_is_remembered_for_the_key(db_session):
    order_id = uuid.uuid4()
    key = charge_key(order_id=order_id)

    first = await provider(db_session, FaultSettings(DECLINE_RATE=1)).charge(
        idempotency_key=key, token="tok_ok", amount_minor=10_000, currency="UAH"
    )
    repeat = await provider(db_session).charge(
        idempotency_key=key, token="tok_ok", amount_minor=10_000, currency="UAH"
    )

    assert first == repeat == ChargeStatus.DECLINED


@pytest.mark.parametrize(
    "changed", [{"amount_minor": 1}, {"currency": "USD"}], ids=["amount", "currency"]
)
async def test_the_same_key_with_other_terms_is_refused(db_session, changed):
    key = charge_key(order_id=uuid.uuid4())
    mock = provider(db_session)
    terms = {"amount_minor": 10_000, "currency": "UAH"}
    await mock.charge(idempotency_key=key, token="tok_ok", **terms)

    with pytest.raises(ConflictException):
        await mock.charge(idempotency_key=key, token="tok_ok", **terms | changed)


async def test_a_charge_key_cannot_be_used_for_a_refund(db_session):
    key = charge_key(order_id=uuid.uuid4())
    mock = provider(db_session)
    await mock.charge(
        idempotency_key=key, token="tok_ok", amount_minor=10_000, currency="UAH"
    )

    with pytest.raises(ConflictException):
        await mock.refund(idempotency_key=key, amount_minor=10_000, currency="UAH")
