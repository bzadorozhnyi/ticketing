from typing import Any

import aiohttp

from core.settings import UpstreamSettings

UNAVAILABLE = {502, 503, 504}


class UpstreamUnavailable(Exception):
    """No definite answer from a service: it may or may not have done the work."""


class HttpClient:
    """Calls one service. Only this class knows the HTTP library."""

    def __init__(self, *, base_url: str, settings: UpstreamSettings):
        self.base_url = base_url
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession(
            base_url=self.base_url,
            timeout=aiohttp.ClientTimeout(
                total=self.settings.TIMEOUT_SECONDS,
                connect=self.settings.CONNECT_TIMEOUT_SECONDS,
            ),
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def request(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        """The status, the JSON body (None when there is none) and the headers.
        A lost connection, a timeout and 502/503/504 raise UpstreamUnavailable.
        """
        if self._session is None:
            raise RuntimeError("HttpClient is not connected")
        try:
            async with self._session.request(
                method, path, headers=headers, json=json, params=params
            ) as response:
                body = (
                    await response.json(content_type=None)
                    if response.status != 204
                    else None
                )
                status, response_headers = response.status, dict(response.headers)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise UpstreamUnavailable(f"{self.base_url}: {exc!r}") from exc
        if status in UNAVAILABLE:
            raise UpstreamUnavailable(f"{self.base_url}{path}: {status}")
        return status, body, response_headers
