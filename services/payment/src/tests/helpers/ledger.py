import uuid

from sqlalchemy import func, select

from payments.models.charge import ChargeStatus
from payments.models.provider_operation import ProviderOperation
from payments.services.pending_charge import charge_key, refund_key


async def money_held(session, order_id: uuid.UUID) -> int:
    """What the provider holds for the order: charged minus refunded, in minor units."""
    charged = await _sum(session, key=charge_key(order_id=order_id))
    refunded = await _sum(session, key=refund_key(order_id=order_id))
    return charged - refunded


async def operations(session, order_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ProviderOperation)
        .where(
            ProviderOperation.idempotency_key.in_(
                [charge_key(order_id=order_id), refund_key(order_id=order_id)]
            )
        )
    )
    return result.scalar_one()


async def _sum(session, *, key: str) -> int:
    result = await session.execute(
        select(func.coalesce(func.sum(ProviderOperation.amount_minor), 0)).where(
            ProviderOperation.idempotency_key == key,
            ProviderOperation.status == ChargeStatus.SUCCEEDED,
        )
    )
    return result.scalar_one()
