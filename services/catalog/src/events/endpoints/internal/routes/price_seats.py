import uuid

from fastapi import status

from core.schemas import error_responses
from events.endpoints.internal.routes.router import router
from events.endpoints.internal.schemas.request.price import PriceSeatsIn
from events.endpoints.internal.schemas.response.price import SeatsPriceOut
from events.use_cases.price_seats import PriceSeatsUseCaseDep


@router.post(
    "/{event_id}/price",
    response_model=SeatsPriceOut,
    responses=error_responses(status.HTTP_404_NOT_FOUND),
)
async def price_seats(
    event_id: uuid.UUID, body: PriceSeatsIn, use_case: PriceSeatsUseCaseDep
) -> SeatsPriceOut:
    dto = await use_case.execute(event_id=event_id, seat_ids=body.seat_ids)
    return SeatsPriceOut.from_dto(dto)
