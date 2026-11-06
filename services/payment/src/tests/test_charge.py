import time
import uuid

import pytest
from sqlalchemy import func, select

from core.exceptions import ConflictException
from core.settings import FaultSettings
from payments.dto.payment import ChargeRequest
from payments.models.charge import Charge, ChargeStatus
from payments.repositories.charge import ChargeRepository
from payments.services.provider import (
    TOKEN_CRASH_AFTER_COMMIT,
    TOKEN_CRASH_BEFORE_COMMIT,
    TOKEN_DECLINED,
    TOKEN_SLOW,
    MockProvider,
)
from payments.use_cases.charge import ChargeUseCase

pytestmark = pytest.mark.anyio


class SimulatedCrash(Exception):
    pass


def raise_crash() -> None:
    raise SimulatedCrash


def use_case(session, faults=None) -> ChargeUseCase:
    return ChargeUseCase(
        session=session,
        charge_repository=ChargeRepository(session=session),
        provider=MockProvider(faults=faults or FaultSettings(), crash=raise_crash),
    )


def charge_request(
    *, order_id=None, token="tok_ok", amount_minor=10_000, currency="UAH"
):
    return ChargeRequest(
        order_id=order_id or uuid.uuid4(),
        amount_minor=amount_minor,
        currency=currency,
        payment_token=token,
    )


async def count_charges(session, order_id) -> int:
    result = await session.execute(
        select(func.count()).select_from(Charge).where(Charge.order_id == order_id)
    )
    return result.scalar_one()


async def test_charges_an_order(db_session):
    request = charge_request()

    result = await use_case(db_session).execute(request=request)

    assert result.status == ChargeStatus.SUCCEEDED
    assert result.order_id == request.order_id
    assert await count_charges(db_session, request.order_id) == 1


async def test_a_repeat_returns_the_first_result_and_takes_no_money(db_session):
    request = charge_request()

    first = await use_case(db_session).execute(request=request)
    second = await use_case(db_session).execute(request=request)

    assert second == first
    assert await count_charges(db_session, request.order_id) == 1


async def test_a_declined_charge_stays_declined_on_a_repeat(db_session):
    request = charge_request(token=TOKEN_DECLINED)

    first = await use_case(db_session).execute(request=request)
    repeat = charge_request(order_id=request.order_id)
    second = await use_case(db_session).execute(request=repeat)

    assert first.status == ChargeStatus.DECLINED
    assert second == first


@pytest.mark.parametrize(
    "changed", [{"amount_minor": 1}, {"currency": "USD"}], ids=["amount", "currency"]
)
async def test_the_same_order_with_other_terms_is_refused(db_session, changed):
    request = charge_request()
    await use_case(db_session).execute(request=request)

    other = charge_request(order_id=request.order_id, **changed)

    with pytest.raises(ConflictException):
        await use_case(db_session).execute(request=other)


async def test_a_crash_before_the_commit_leaves_nothing(db_session):
    crashing = charge_request(token=TOKEN_CRASH_BEFORE_COMMIT)

    with pytest.raises(SimulatedCrash):
        await use_case(db_session).execute(request=crashing)

    assert await count_charges(db_session, crashing.order_id) == 0

    repeat = charge_request(order_id=crashing.order_id)
    result = await use_case(db_session).execute(request=repeat)
    assert result.status == ChargeStatus.SUCCEEDED


async def test_a_crash_after_the_commit_keeps_the_charge(db_session):
    crashing = charge_request(token=TOKEN_CRASH_AFTER_COMMIT)

    with pytest.raises(SimulatedCrash):
        await use_case(db_session).execute(request=crashing)

    assert await count_charges(db_session, crashing.order_id) == 1

    repeat = charge_request(order_id=crashing.order_id, token=TOKEN_CRASH_AFTER_COMMIT)
    result = await use_case(db_session).execute(request=repeat)
    assert result.status == ChargeStatus.SUCCEEDED
    assert await count_charges(db_session, crashing.order_id) == 1


async def test_a_late_charge_after_a_cancellation_takes_no_money(
    db_session, charge_factory
):
    cancelled = await charge_factory(status=ChargeStatus.CANCELLED)

    result = await use_case(db_session).execute(
        request=charge_request(order_id=cancelled.order_id)
    )

    assert result.status == ChargeStatus.CANCELLED


async def test_a_slow_token_waits_for_the_configured_time(db_session):
    faults = FaultSettings(SLOW_SECONDS=0.05)

    started = time.perf_counter()
    result = await use_case(db_session, faults).execute(
        request=charge_request(token=TOKEN_SLOW)
    )

    assert time.perf_counter() - started >= 0.05
    assert result.status == ChargeStatus.SUCCEEDED


async def test_a_decline_rate_applies_to_any_token(db_session):
    faults = FaultSettings(DECLINE_RATE=1)

    result = await use_case(db_session, faults).execute(request=charge_request())

    assert result.status == ChargeStatus.DECLINED


@pytest.mark.parametrize(
    "faults",
    [
        FaultSettings(CRASH_BEFORE_COMMIT_RATE=1),
        FaultSettings(CRASH_AFTER_COMMIT_RATE=1),
    ],
    ids=["before", "after"],
)
async def test_a_crash_rate_applies_to_any_token(db_session, faults):
    with pytest.raises(SimulatedCrash):
        await use_case(db_session, faults).execute(request=charge_request())
