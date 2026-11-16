from orders.endpoints.internal.routes.router import router
from orders.endpoints.internal.schemas.request.existing import ExistingOrdersIn
from orders.endpoints.internal.schemas.response.existing import ExistingOrdersOut
from orders.use_cases.find_existing_orders import FindExistingOrdersUseCaseDep


@router.post("/existing", response_model=ExistingOrdersOut)
async def find_existing_orders(
    body: ExistingOrdersIn, use_case: FindExistingOrdersUseCaseDep
) -> ExistingOrdersOut:
    """Which of these orders booking has: payment checks its charges against them."""
    existing = await use_case.execute(order_ids=body.order_ids)
    return ExistingOrdersOut(order_ids=sorted(existing))
