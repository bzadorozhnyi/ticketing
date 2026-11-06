from collections.abc import Collection
from typing import Any

import aiohttp

from core.settings import UpstreamSettings


class UpstreamError(Exception):
    """No definite answer: the work may have happened or not, so the step repeats."""


class HttpClient:
    """Calls one upstream. Only this class knows the HTTP library."""

    def __init__(self, *, base_url: str, settings: UpstreamSettings):
        self.base_url = base_url
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("HttpClient is not connected")
        return self._session

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
        expected: Collection[int],
        headers: dict[str, str] | None = None,
        json: Any = None,
    ) -> tuple[int, Any]:
        """A status outside `expected` raises UpstreamError, so a 503 is never read
        as an answer.
        """
        try:
            async with self.session.request(
                method, path, headers=headers, json=json
            ) as response:
                if response.status not in expected:
                    raise UpstreamError(f"{method} {path}: {response.status}")
                return response.status, await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise UpstreamError(f"{method} {path}: {exc!r}") from exc
