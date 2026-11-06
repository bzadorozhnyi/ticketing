import asyncio
import os
import random
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from core.settings import FaultSettings, settings
from payments.models.charge import ChargeStatus

TOKEN_DECLINED = "tok_declined"
TOKEN_SLOW = "tok_slow"
TOKEN_CRASH_BEFORE_COMMIT = "tok_crash_before_commit"
TOKEN_CRASH_AFTER_COMMIT = "tok_crash_after_commit"


def kill_process() -> None:
    """A real crash: no cleanup, no rollback code, like a killed process."""
    os._exit(1)


class MockProvider:
    """The mock payment provider. A token gives a repeatable fault for tests, and the
    rates in settings give random faults for experiments. Delays and crashes need
    `armed`: on a redelivery they stay off, or a crash would repeat on every attempt.
    """

    def __init__(
        self,
        *,
        faults: FaultSettings,
        crash: Callable[[], None] = kill_process,
        armed: bool = True,
    ):
        self.faults = faults
        self.crash = crash
        self.armed = armed

    def _hit(self, *, token: str, fault_token: str, rate: float) -> bool:
        return token == fault_token or random.random() < rate

    async def authorize(self, *, token: str) -> ChargeStatus:
        if token == TOKEN_SLOW and self.armed:
            await asyncio.sleep(self.faults.SLOW_SECONDS)
        if self.armed and self._hit(
            token=token,
            fault_token=TOKEN_CRASH_BEFORE_COMMIT,
            rate=self.faults.CRASH_BEFORE_COMMIT_RATE,
        ):
            self.crash()
        if self._hit(
            token=token, fault_token=TOKEN_DECLINED, rate=self.faults.DECLINE_RATE
        ):
            return ChargeStatus.DECLINED
        return ChargeStatus.SUCCEEDED

    def after_commit(self, *, token: str) -> None:
        if self.armed and self._hit(
            token=token,
            fault_token=TOKEN_CRASH_AFTER_COMMIT,
            rate=self.faults.CRASH_AFTER_COMMIT_RATE,
        ):
            self.crash()


def get_mock_provider() -> MockProvider:
    return MockProvider(faults=settings.FAULTS)


MockProviderDep = Annotated[MockProvider, Depends(get_mock_provider)]
