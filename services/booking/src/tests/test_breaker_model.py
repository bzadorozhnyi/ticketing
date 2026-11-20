"""The circuit breaker against a model of its rules, on sequences of calls, results,
cancellations and time that Hypothesis makes up, with settings it makes up too.

The model is the breaker's docstring written out: a window of the last results of
calls made while closed, opening at the failure share once enough calls were seen, one
probe after the open time, and a call that counts once. After every step the breaker
must answer as the model does: let the call through, let it through as the probe, or
refuse it and say how long to wait.
"""

from typing import Literal

import pytest
from hypothesis import event, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    multiple,
    precondition,
    rule,
)

from core.breaker import BreakerCall, CircuitBreaker, CircuitOpenError
from core.settings import BreakerSettings

Answer = Literal["call", "probe", "refused"]


class Model:
    def __init__(self, settings: BreakerSettings):
        self.settings = settings
        self.mode: Literal["closed", "open", "half_open"] = "closed"
        self.open_until = 0.0
        self.probe_out = False
        # Results of calls made while closed, oldest first, at most WINDOW of them.
        self.window: list[bool] = []

    def acquire(self, now: float) -> tuple[Answer, float]:
        if self.mode == "closed":
            return "call", 0
        if self.mode == "open" and now < self.open_until:
            event("refused while open")
            return "refused", self.open_until - now
        if self.probe_out:
            event("refused while a probe is out")
            return "refused", self.settings.OPEN_SECONDS
        self.mode = "half_open"
        self.probe_out = True
        event("probe")
        return "probe", 0

    def finish(self, *, probe: bool, failed: bool, now: float) -> None:
        if probe:
            self.probe_out = False
            event(f"probe {'failed' if failed else 'succeeded'}")
            if failed:
                self._open(now)
            else:
                self.mode = "closed"
            return
        if self.mode != "closed":
            event("late result ignored")
            return
        self.window = [*self.window, failed][-self.settings.WINDOW :]
        calls, failures = len(self.window), sum(self.window)
        enough = calls >= self.settings.MIN_CALLS
        if enough and failures * 100 >= self.settings.FAILURE_PERCENT * calls:
            self._open(now)

    def release(self, *, probe: bool) -> None:
        """A cancelled probe frees its place; the breaker stays half open."""
        if probe:
            self.probe_out = False

    def _open(self, now: float) -> None:
        event("opened")
        self.mode = "open"
        self.open_until = now + self.settings.OPEN_SECONDS
        self.window = []


class Tracked:
    """A call the breaker gave out, and whether it has counted yet."""

    def __init__(self, call: BreakerCall, *, probe: bool):
        self.call = call
        self.probe = probe
        self.done = False


@st.composite
def breaker_settings(draw: st.DrawFn) -> BreakerSettings:
    """MIN_CALLS above WINDOW would never open: the window cannot hold enough calls."""
    window = draw(st.integers(1, 8))
    return BreakerSettings(
        WINDOW=window,
        MIN_CALLS=draw(st.integers(1, window)),
        FAILURE_PERCENT=draw(st.integers(1, 100)),
        OPEN_SECONDS=draw(st.integers(1, 30)),
    )


class BreakerMachine(RuleBasedStateMachine):
    calls = Bundle("calls")

    @initialize(settings=breaker_settings())
    def setup(self, settings: BreakerSettings):
        self.now = 1000.0
        self.breaker = CircuitBreaker(
            upstream="seat-hold", settings=settings, clock=lambda: self.now
        )
        self.model = Model(settings)
        self.given_out: list[Tracked] = []

    @rule(target=calls)
    def acquire(self):
        expected, wait = self.model.acquire(self.now)
        try:
            call = self.breaker.acquire()
        except CircuitOpenError as refused:
            assert expected == "refused"
            assert refused.retry_after == pytest.approx(wait)
            return multiple()
        assert expected != "refused"
        tracked = Tracked(call, probe=expected == "probe")
        self.given_out.append(tracked)
        return tracked

    @rule(tracked=calls, failed=st.booleans())
    def finish(self, tracked: Tracked, failed: bool):
        """Finishing twice is allowed; only the first one counts."""
        tracked.call.finish(failed=failed)
        if not tracked.done:
            tracked.done = True
            self.model.finish(probe=tracked.probe, failed=failed, now=self.now)

    @precondition(lambda self: self._probe() is not None)
    @rule(failed=st.booleans())
    def finish_the_probe(self, failed: bool):
        """The probe is one of many calls given out; without this rule the generator
        rarely picks it, and a probe that closes the breaker would hardly be tried."""
        tracked = self._probe()
        assert tracked is not None
        self.finish(tracked, failed)

    def _probe(self) -> Tracked | None:
        return next((t for t in self.given_out if t.probe and not t.done), None)

    @rule(tracked=calls)
    def release(self, tracked: Tracked):
        """A cancelled call: only an unfinished probe frees its place."""
        tracked.call.release()
        if tracked.probe and not tracked.done:
            tracked.done = True
            self.model.release(probe=True)

    @rule(seconds=st.floats(0, 40, allow_nan=False))
    def wait(self, seconds: float):
        self.now += seconds

    @invariant()
    def at_most_one_probe_is_out(self):
        out = [t for t in self.given_out if t.probe and not t.done]
        assert len(out) <= 1


TestBreakerAgainstItsModel = BreakerMachine.TestCase
TestBreakerAgainstItsModel.settings = settings(max_examples=300)
