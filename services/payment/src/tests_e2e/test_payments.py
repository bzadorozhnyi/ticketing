import asyncio
import os
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select

from core.exceptions import ConflictException
from core.settings import FaultSettings
from payments.dto.payment import ChargeDTO, ChargeRequest
from payments.models.charge import Charge, ChargeStatus
from payments.models.refund import Refund
from payments.repositories.charge import ChargeRepository
from payments.repositories.refund import RefundRepository
from payments.services.pending_charge import PendingChargeFinisher, charge_key
from payments.services.provider import (
    TOKEN_CRASH_AFTER_COMMIT,
    TOKEN_CRASH_BEFORE_COMMIT,
    TOKEN_SLOW,
    MockProvider,
)
from payments.use_cases.charge import ChargeUseCase
from payments.use_cases.refund import RefundUseCase
from tests.helpers.ledger import money_held, operations

pytestmark = pytest.mark.anyio

SRC_PATH = Path(__file__).resolve().parent.parent


def provider(session_maker, slow_seconds: float = 3, armed: bool = True):
    """`armed=False` is a redelivery: the consumer turns faults off for it."""
    return MockProvider(
        session_maker=session_maker,
        faults=FaultSettings(SLOW_SECONDS=slow_seconds),
        armed=armed,
    )


async def charge(
    session_maker,
    order_id,
    token="tok_ok",
    amount_minor=10_000,
    slow_seconds=3,
    armed=True,
):
    async with session_maker() as session:
        charges = ChargeRepository(session=session)
        mock = provider(session_maker, slow_seconds, armed)
        use_case = ChargeUseCase(
            session=session,
            charge_repository=charges,
            finisher=PendingChargeFinisher(
                session=session, charge_repository=charges, provider=mock
            ),
            provider=mock,
        )
        return await use_case.execute(
            request=ChargeRequest(
                order_id=order_id,
                amount_minor=amount_minor,
                currency="UAH",
                payment_token=token,
            )
        )


async def refund(session_maker, order_id):
    async with session_maker() as session:
        charges = ChargeRepository(session=session)
        mock = provider(session_maker)
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
            order_id=order_id, amount_minor=10_000, currency="UAH"
        )


async def stored_charge(session_maker, order_id) -> Charge | None:
    async with session_maker() as session:
        result = await session.execute(
            select(Charge).where(Charge.order_id == order_id)
        )
        return result.scalar_one_or_none()


async def count_refunds(session_maker, charge_id) -> int:
    async with session_maker() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Refund)
            .where(Refund.charge_id == charge_id)
        )
        return result.scalar_one()


def run_in_a_new_process(order_id, token) -> int:
    process = subprocess.run(
        [sys.executable, "-m", "tests_e2e.charge_once", str(order_id), token],
        cwd=SRC_PATH,
        env=os.environ | {"PYTHONPATH": str(SRC_PATH)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return process.returncode


async def test_concurrent_duplicates_take_money_once(session_maker):
    order_id = uuid.uuid4()

    results = await asyncio.gather(
        *(charge(session_maker, order_id) for _ in range(50))
    )

    assert len({result.charge_id for result in results}) == 1
    assert {result.status for result in results} == {ChargeStatus.SUCCEEDED}
    stored = await stored_charge(session_maker, order_id)
    assert stored is not None
    assert stored.status == ChargeStatus.SUCCEEDED
    async with session_maker() as session:
        assert await money_held(session, order_id) == 10_000


async def test_concurrent_calls_with_one_key_leave_one_provider_operation(
    session_maker,
):
    order_id = uuid.uuid4()
    mock = provider(session_maker)

    statuses = await asyncio.gather(
        *(
            mock.charge(
                idempotency_key=charge_key(order_id=order_id),
                token="tok_ok",
                amount_minor=10_000,
                currency="UAH",
            )
            for _ in range(30)
        )
    )

    assert set(statuses) == {ChargeStatus.SUCCEEDED}
    async with session_maker() as session:
        assert await operations(session, order_id) == 1
        assert await money_held(session, order_id) == 10_000


async def test_concurrent_charges_with_other_terms_keep_one_amount(session_maker):
    order_id = uuid.uuid4()
    amounts = [10_000 + index % 2 for index in range(20)]

    results = await asyncio.gather(
        *(charge(session_maker, order_id, amount_minor=amount) for amount in amounts),
        return_exceptions=True,
    )

    conflicts = [result for result in results if isinstance(result, ConflictException)]
    assert len(conflicts) == 10
    stored = await stored_charge(session_maker, order_id)
    assert stored is not None
    won = [result for result in results if isinstance(result, ChargeDTO)]
    assert {result.charge_id for result in won} == {stored.id}


async def test_concurrent_refunds_return_money_once(session_maker):
    order_id = uuid.uuid4()
    first = await charge(session_maker, order_id)

    await asyncio.gather(*(refund(session_maker, order_id) for _ in range(50)))

    assert await count_refunds(session_maker, first.charge_id) == 1
    async with session_maker() as session:
        assert await money_held(session, order_id) == 0


async def test_a_racing_charge_and_refund_never_keep_the_money(session_maker):
    orders = [uuid.uuid4() for _ in range(50)]

    await asyncio.gather(
        *(charge(session_maker, order_id) for order_id in orders),
        *(refund(session_maker, order_id) for order_id in orders),
        return_exceptions=True,
    )

    outcomes = Counter()
    for order_id in orders:
        stored = await stored_charge(session_maker, order_id)
        async with session_maker() as session:
            held = await money_held(session, order_id)
        outcomes[(stored.status if stored else None, held)] += 1
    assert all(held == 0 for _, held in outcomes), outcomes


async def test_a_refund_during_a_slow_charge_returns_the_money(session_maker):
    """Before the reservation this left `cancelled` with the money taken: 10 of 10."""
    orders = [uuid.uuid4() for _ in range(10)]

    async def late_refund(order_id):
        await asyncio.sleep(0.05)
        return await refund(session_maker, order_id)

    await asyncio.gather(
        *(
            charge(session_maker, order_id, token=TOKEN_SLOW, slow_seconds=0.2)
            for order_id in orders
        ),
        *(late_refund(order_id) for order_id in orders),
    )

    for order_id in orders:
        stored = await stored_charge(session_maker, order_id)
        assert stored is not None
        assert stored.status == ChargeStatus.SUCCEEDED
        assert await count_refunds(session_maker, stored.id) == 1
        async with session_maker() as session:
            assert await money_held(session, order_id) == 0


async def test_a_killed_process_after_the_provider_took_the_money_charges_once(
    session_maker,
):
    order_id = uuid.uuid4()

    code = await asyncio.to_thread(
        run_in_a_new_process, order_id, TOKEN_CRASH_BEFORE_COMMIT
    )
    assert code == 1
    stored = await stored_charge(session_maker, order_id)
    assert stored is not None
    assert stored.status == ChargeStatus.PENDING

    repeat = await charge(session_maker, order_id, armed=False)

    assert repeat.status == ChargeStatus.SUCCEEDED
    finished = await stored_charge(session_maker, order_id)
    assert finished is not None
    assert finished.status == ChargeStatus.SUCCEEDED
    async with session_maker() as session:
        assert await money_held(session, order_id) == 10_000
        assert await operations(session, order_id) == 1


async def test_a_killed_process_after_the_commit_keeps_the_charge(session_maker):
    order_id = uuid.uuid4()

    code = await asyncio.to_thread(
        run_in_a_new_process, order_id, TOKEN_CRASH_AFTER_COMMIT
    )

    assert code == 1
    stored = await stored_charge(session_maker, order_id)
    assert stored is not None
    repeat = await charge(session_maker, order_id)
    assert repeat.charge_id == stored.id
