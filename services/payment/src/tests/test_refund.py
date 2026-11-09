import uuid

import pytest
from sqlalchemy import func, select

from core.settings import FaultSettings
from payments.dto.payment import RefundStatus
from payments.models.charge import Charge, ChargeStatus
from payments.models.refund import Refund
from payments.repositories.charge import ChargeRepository
from payments.repositories.refund import RefundRepository
from payments.services.pending_charge import PendingChargeFinisher
from payments.services.provider import TOKEN_DECLINED, MockProvider
from payments.use_cases.refund import RefundUseCase
from tests.helpers.ledger import money_held, operations
from tests.helpers.provider import provider_sessions

pytestmark = pytest.mark.anyio


def use_case(session) -> RefundUseCase:
    charges = ChargeRepository(session=session)
    provider = MockProvider(
        session_maker=provider_sessions(session), faults=FaultSettings()
    )
    return RefundUseCase(
        session=session,
        charge_repository=charges,
        refund_repository=RefundRepository(session=session),
        finisher=PendingChargeFinisher(
            session=session, charge_repository=charges, provider=provider
        ),
        provider=provider,
    )


async def refund(session, order_id) -> RefundStatus:
    return await use_case(session).execute(
        order_id=order_id, amount_minor=10_000, currency="UAH"
    )


async def count_refunds(session, charge_id) -> int:
    result = await session.execute(
        select(func.count()).select_from(Refund).where(Refund.charge_id == charge_id)
    )
    return result.scalar_one()


async def test_refunds_a_successful_charge(db_session, charge_factory):
    charge = await charge_factory()

    status = await refund(db_session, charge.order_id)

    assert status == RefundStatus.REFUNDED
    assert await count_refunds(db_session, charge.id) == 1
    assert await money_held(db_session, charge.order_id) == -10_000
    assert await operations(db_session, charge.order_id) == 1


async def test_a_repeated_refund_returns_money_once(db_session, charge_factory):
    charge = await charge_factory()

    await refund(db_session, charge.order_id)
    status = await refund(db_session, charge.order_id)

    assert status == RefundStatus.REFUNDED
    assert await count_refunds(db_session, charge.id) == 1
    assert await operations(db_session, charge.order_id) == 1


@pytest.mark.parametrize("status", [ChargeStatus.DECLINED, ChargeStatus.CANCELLED])
async def test_nothing_to_refund_without_money(db_session, charge_factory, status):
    charge = await charge_factory(status=status)

    result = await refund(db_session, charge.order_id)

    assert result == RefundStatus.NOTHING_TO_REFUND
    assert await count_refunds(db_session, charge.id) == 0


async def test_a_refund_finishes_a_pending_charge_and_returns_it(
    db_session, charge_factory
):
    pending = await charge_factory(status=ChargeStatus.PENDING, payment_token="tok_ok")

    status = await refund(db_session, pending.order_id)

    assert status == RefundStatus.REFUNDED
    stored = await db_session.execute(
        select(Charge.status).where(Charge.id == pending.id)
    )
    assert stored.scalar_one() == ChargeStatus.SUCCEEDED
    assert await count_refunds(db_session, pending.id) == 1
    assert await money_held(db_session, pending.order_id) == 0
    assert await operations(db_session, pending.order_id) == 2


async def test_a_refund_of_a_pending_declined_charge_returns_nothing(
    db_session, charge_factory
):
    pending = await charge_factory(
        status=ChargeStatus.PENDING, payment_token=TOKEN_DECLINED
    )

    status = await refund(db_session, pending.order_id)

    assert status == RefundStatus.NOTHING_TO_REFUND
    assert await count_refunds(db_session, pending.id) == 0
    assert await money_held(db_session, pending.order_id) == 0


async def test_a_refund_before_the_charge_cancels_the_order(db_session):
    order_id = uuid.uuid4()

    result = await refund(db_session, order_id)

    assert result == RefundStatus.NOTHING_TO_REFUND
    stored = await db_session.execute(select(Charge).where(Charge.order_id == order_id))
    assert stored.scalar_one().status == ChargeStatus.CANCELLED
