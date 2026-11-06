from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from orders.dto.messages import RefundResult
from orders.models.order import SagaStep
from orders.repositories.order import OrderRepository


class ApplyRefundResultUseCase:
    def __init__(self, *, session: AsyncSession, order_repository: OrderRepository):
        self.session = session
        self.order_repository = order_repository

    async def execute(self, *, result: RefundResult) -> None:
        """Accepted only while the saga waits for it. Either status ends the order:
        the money is back, or it was never taken. The reason was stored with the step.
        """
        order = await self.order_repository.lock(order_id=result.order_id)
        if order is None or order.saga_step != SagaStep.AWAITING_REFUND:
            logger.bind(order_id=str(result.order_id)).info("Refund result ignored")
            await self.session.rollback()
            return

        await self.order_repository.mark_refund_finished(order_id=order.id)
        await self.session.commit()
