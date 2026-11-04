from fastapi import APIRouter, status

from core.schemas import error_responses

router = APIRouter(
    prefix="/v1/events",
    tags=["events"],
    responses=error_responses(
        status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_503_SERVICE_UNAVAILABLE
    ),
)
