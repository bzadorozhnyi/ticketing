import time
from collections import deque
from collections.abc import Callable
from enum import StrEnum, auto

from loguru import logger

from core.settings import BreakerSettings


class CircuitOpenError(Exception):
    """The upstream was not called: it kept failing. Try again after `retry_after`."""

    def __init__(self, *, upstream: str, retry_after: float):
        self.upstream = upstream
        self.retry_after = retry_after
        super().__init__(f"Circuit to {upstream} is open for {retry_after:.1f} s")


class Mode(StrEnum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class BreakerCall:
    """Permission for one call. A probe that never finishes, because its task was
    cancelled, is released in `release`, or the breaker would wait for it for ever.
    """

    def __init__(self, *, breaker: CircuitBreaker, probe: bool):
        self._breaker = breaker
        self._probe = probe
        self._finished = False

    def finish(self, *, failed: bool) -> None:
        if self._finished:
            return
        self._finished = True
        self._breaker._record(probe=self._probe, failed=failed)

    def release(self) -> None:
        if self._probe and not self._finished:
            self._finished = True
            self._breaker._release_probe()


class CircuitBreaker:
    """Stops calling an upstream that keeps failing, so the saga does not wait for
    timeouts and the upstream gets time to recover. Opens when `FAILURE_PERCENT` of the
    last `WINDOW` calls failed, at least `MIN_CALLS` of them; stays open for
    `OPEN_SECONDS`, then lets one probe through. One breaker per upstream per process.
    """

    def __init__(
        self,
        *,
        upstream: str,
        settings: BreakerSettings,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.upstream = upstream
        self.settings = settings
        self.clock = clock
        self._mode = Mode.CLOSED
        self._open_until = 0.0
        self._probing = False
        self._results: deque[bool] = deque(maxlen=settings.WINDOW)

    def acquire(self) -> BreakerCall:
        """A call, or CircuitOpenError while the breaker is open or a probe is out."""
        now = self.clock()
        if self._mode == Mode.CLOSED:
            return BreakerCall(breaker=self, probe=False)
        if self._mode == Mode.OPEN and now < self._open_until:
            raise CircuitOpenError(
                upstream=self.upstream, retry_after=self._open_until - now
            )
        if self._probing:
            raise CircuitOpenError(
                upstream=self.upstream, retry_after=self.settings.OPEN_SECONDS
            )
        self._mode = Mode.HALF_OPEN
        self._probing = True
        return BreakerCall(breaker=self, probe=True)

    def _record(self, *, probe: bool, failed: bool) -> None:
        if probe:
            self._probing = False
            if failed:
                self._open()
            else:
                self._mode = Mode.CLOSED
                logger.bind(upstream=self.upstream).info("Circuit closed")
            return
        # A call that started before the breaker opened must not keep it open longer.
        if self._mode != Mode.CLOSED:
            return
        self._results.append(failed)
        calls = len(self._results)
        failures = sum(self._results)
        if (
            calls >= self.settings.MIN_CALLS
            and failures * 100 >= self.settings.FAILURE_PERCENT * calls
        ):
            self._open()

    def _open(self) -> None:
        self._mode = Mode.OPEN
        self._open_until = self.clock() + self.settings.OPEN_SECONDS
        self._results.clear()
        logger.bind(upstream=self.upstream).warning("Circuit opened")

    def _release_probe(self) -> None:
        self._probing = False
