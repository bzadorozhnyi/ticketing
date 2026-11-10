from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from orders.dto.messages import ChargeResult, ChargeStatus
from orders.dto.order import FinishedOrder
from orders.models.order import FailureReason, OrderStatus, SagaStep
from orders.repositories.order import OrderRepository


class ApplyChargeResultUseCase:
    def __init__(self, *, session: AsyncSession, order_repository: OrderRepository):
        self.session = session
        self.order_repository = order_repository

    async def execute(self, *, result: ChargeResult) -> FinishedOrder | None:
        """Accepted only while the saga waits for it: a repeat or a late reply finds
        another step and changes nothing. A declined card leaves the hold alone,
        so the user can try again.
        """
        order = await self.order_repository.lock(order_id=result.order_id)
        if order is None or order.saga_step != SagaStep.AWAITING_CHARGE:
            logger.bind(order_id=str(result.order_id)).info("Charge result ignored")
            await self.session.rollback()
            return None

        finished = None
        if result.status == ChargeStatus.SUCCEEDED:
            await self.order_repository.mark_charged(order_id=order.id)
        elif result.status == ChargeStatus.DECLINED:
            await self.order_repository.mark_failed(
                order_id=order.id, reason=FailureReason.CARD_DECLINED
            )
            finished = FinishedOrder(
                status=OrderStatus.FAILED, created_at=order.created_at
            )
        else:
            logger.bind(order_id=str(order.id), status=result.status).error(
                "Charge was cancelled while the saga waits for it"
            )
        await self.session.commit()
        return finished
