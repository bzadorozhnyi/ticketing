import uuid

from fastapi import status

from core.http import HttpClient
from orders.dto.upstream import HoldDTO


class SeatHoldClient:
    def __init__(self, *, http: HttpClient):
        self.http = http

    async def get_hold(self, *, hold_id: uuid.UUID, user_id: str) -> HoldDTO | None:
        """None means the user has no such hold."""
        code, body = await self.http.request(
            method="GET",
            path=f"/internal/v1/holds/{hold_id}",
            expected={status.HTTP_200_OK, status.HTTP_404_NOT_FOUND},
            headers={"X-User-Id": user_id},
        )
        if code == status.HTTP_404_NOT_FOUND:
            return None
        return HoldDTO.model_validate(body)

    async def extend(self, *, hold_id: uuid.UUID, user_id: str) -> bool:
        """False means the hold is gone: no money may be taken."""
        code, _ = await self.http.request(
            method="POST",
            path=f"/internal/v1/holds/{hold_id}/extend",
            expected={status.HTTP_200_OK, status.HTTP_409_CONFLICT},
            headers={"X-User-Id": user_id},
        )
        return code == status.HTTP_200_OK
