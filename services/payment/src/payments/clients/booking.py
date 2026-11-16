import uuid

import aiohttp


class BookingUnavailableError(Exception):
    """No definite answer from booking: the check learns nothing this time."""


class BookingClient:
    """Asks booking which orders exist (`POST /internal/v1/orders/existing`). Only this
    class knows the HTTP library.
    """

    def __init__(self, *, base_url: str, timeout_seconds: float):
        self.base_url = base_url
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def existing(self, *, order_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        try:
            async with (
                aiohttp.ClientSession(
                    base_url=self.base_url, timeout=self.timeout
                ) as http,
                http.post(
                    "/internal/v1/orders/existing",
                    json={"order_ids": [str(order_id) for order_id in order_ids]},
                ) as response,
            ):
                if response.status != 200:
                    raise BookingUnavailableError(f"booking answered {response.status}")
                body = await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BookingUnavailableError(repr(exc)) from exc
        return {uuid.UUID(order_id) for order_id in body["order_ids"]}
