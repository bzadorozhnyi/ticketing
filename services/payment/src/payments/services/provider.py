import asyncio
import os
import random
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db import async_session
from core.exceptions import ConflictException, InternalServerException
from core.settings import FaultSettings, settings
from payments.models.charge import ChargeStatus
from payments.models.provider_operation import (
    ProviderOperation,
    ProviderOperationKind,
)
from payments.repositories.provider_operation import ProviderOperationRepository

TOKEN_DECLINED = "tok_declined"
TOKEN_SLOW = "tok_slow"
TOKEN_CRASH_BEFORE_COMMIT = "tok_crash_before_commit"
TOKEN_CRASH_AFTER_COMMIT = "tok_crash_after_commit"


def kill_process() -> None:
    """A real crash: no cleanup, no rollback code, like a killed process."""
    os._exit(1)


class MockProvider:
    """The mock payment provider. Like a real one, it remembers every operation by its
    idempotency key in its own storage, committed apart from ours: a repeat with the
    same key returns the first answer and takes no money.

    A token gives a repeatable fault for tests, and the rates in settings give random
    faults for experiments. Delays and crashes need `armed`: on a redelivery they stay
    off, or a crash would repeat on every attempt.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        faults: FaultSettings,
        crash: Callable[[], None] = kill_process,
        armed: bool = True,
    ):
        self.session_maker = session_maker
        self.faults = faults
        self.crash = crash
        self.armed = armed

    def _hit(self, *, token: str, fault_token: str, rate: float) -> bool:
        return token == fault_token or random.random() < rate

    async def charge(
        self, *, idempotency_key: str, token: str, amount_minor: int, currency: str
    ) -> ChargeStatus:
        """The crash token kills the process after the money is taken and before our
        side has stored it: the window a real integration has to survive.
        """
        if token == TOKEN_SLOW and self.armed:
            await asyncio.sleep(self.faults.SLOW_SECONDS)
        declined = self._hit(
            token=token, fault_token=TOKEN_DECLINED, rate=self.faults.DECLINE_RATE
        )
        operation = await self._record(
            idempotency_key=idempotency_key,
            kind=ProviderOperationKind.CHARGE,
            amount_minor=amount_minor,
            currency=currency,
            status=ChargeStatus.DECLINED if declined else ChargeStatus.SUCCEEDED,
        )
        if self.armed and self._hit(
            token=token,
            fault_token=TOKEN_CRASH_BEFORE_COMMIT,
            rate=self.faults.CRASH_BEFORE_COMMIT_RATE,
        ):
            self.crash()
        return ChargeStatus(operation.status)

    async def refund(
        self, *, idempotency_key: str, amount_minor: int, currency: str
    ) -> None:
        await self._record(
            idempotency_key=idempotency_key,
            kind=ProviderOperationKind.REFUND,
            amount_minor=amount_minor,
            currency=currency,
            status=ChargeStatus.SUCCEEDED,
        )

    def after_commit(self, *, token: str) -> None:
        if self.armed and self._hit(
            token=token,
            fault_token=TOKEN_CRASH_AFTER_COMMIT,
            rate=self.faults.CRASH_AFTER_COMMIT_RATE,
        ):
            self.crash()

    async def _record(
        self,
        *,
        idempotency_key: str,
        kind: ProviderOperationKind,
        amount_minor: int,
        currency: str,
        status: ChargeStatus,
    ) -> ProviderOperation:
        async with self.session_maker() as session:
            repository = ProviderOperationRepository(session=session)
            await repository.insert_if_absent(
                idempotency_key=idempotency_key,
                kind=kind,
                amount_minor=amount_minor,
                currency=currency,
                status=status,
            )
            await session.commit()
            operation = await repository.get_by_key(idempotency_key=idempotency_key)
        if operation is None:
            raise InternalServerException(f"Operation {idempotency_key} is missing")
        same_terms = (
            operation.kind == kind
            and operation.amount_minor == amount_minor
            and operation.currency == currency
        )
        if not same_terms:
            raise ConflictException(f"Key {idempotency_key} was used with other terms")
        return operation


def get_mock_provider() -> MockProvider:
    return MockProvider(session_maker=async_session, faults=settings.FAULTS)


MockProviderDep = Annotated[MockProvider, Depends(get_mock_provider)]
