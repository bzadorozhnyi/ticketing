import pytest

from core.breaker import CircuitBreaker, CircuitOpenError
from core.settings import BreakerSettings

OPEN_SECONDS = 10


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_breaker(clock: Clock, window: int = 4, min_calls: int = 4) -> CircuitBreaker:
    settings = BreakerSettings(
        WINDOW=window,
        MIN_CALLS=min_calls,
        FAILURE_PERCENT=50,
        OPEN_SECONDS=OPEN_SECONDS,
    )
    return CircuitBreaker(upstream="seat-hold", settings=settings, clock=clock)


def calls(breaker: CircuitBreaker, failures: list[bool]) -> None:
    for failed in failures:
        breaker.acquire().finish(failed=failed)


def is_open(breaker: CircuitBreaker) -> bool:
    try:
        breaker.acquire().release()
    except CircuitOpenError:
        return True
    return False


def test_opens_at_the_failure_share_once_enough_calls_were_seen():
    breaker = make_breaker(Clock())

    calls(breaker, [True, False, True])
    assert not is_open(breaker), "three calls are below the minimum"
    calls(breaker, [False])

    assert is_open(breaker)


def test_stays_closed_below_the_failure_share():
    breaker = make_breaker(Clock(), window=10, min_calls=10)

    calls(breaker, [True, False, False] * 3 + [False])

    assert not is_open(breaker)


def test_only_the_last_calls_count():
    breaker = make_breaker(Clock())

    calls(breaker, [False] * 8 + [True, True])

    assert is_open(breaker), "two of the last four failed, though only two of ten"


def test_an_open_breaker_says_how_long_to_wait():
    clock = Clock()
    breaker = make_breaker(clock)
    calls(breaker, [True] * 4)
    clock.now += 3

    with pytest.raises(CircuitOpenError) as refused:
        breaker.acquire()

    assert refused.value.retry_after == pytest.approx(OPEN_SECONDS - 3)
    assert refused.value.upstream == "seat-hold"


def test_after_the_open_time_one_probe_goes_and_its_success_closes_it():
    clock = Clock()
    breaker = make_breaker(clock)
    calls(breaker, [True] * 4)
    clock.now += OPEN_SECONDS

    probe = breaker.acquire()
    assert is_open(breaker), "only one probe at a time"
    probe.finish(failed=False)

    assert not is_open(breaker)


def test_a_failed_probe_opens_it_for_another_full_period():
    clock = Clock()
    breaker = make_breaker(clock)
    calls(breaker, [True] * 4)
    clock.now += OPEN_SECONDS

    breaker.acquire().finish(failed=True)
    clock.now += OPEN_SECONDS - 1

    assert is_open(breaker)


def test_a_released_probe_lets_the_next_request_probe():
    clock = Clock()
    breaker = make_breaker(clock)
    calls(breaker, [True] * 4)
    clock.now += OPEN_SECONDS

    breaker.acquire().release()

    breaker.acquire().finish(failed=False)
    assert not is_open(breaker)


def test_a_call_counts_once_however_often_it_is_finished():
    breaker = make_breaker(Clock())

    for _ in range(4):
        call = breaker.acquire()
        call.finish(failed=True)
        call.finish(failed=False)

    assert is_open(breaker)


def test_late_failures_of_calls_started_before_it_opened_do_not_keep_it_open():
    clock = Clock()
    breaker = make_breaker(clock, window=2, min_calls=2)
    early = [breaker.acquire() for _ in range(2)]
    late = [breaker.acquire() for _ in range(2)]
    for call in early:
        call.finish(failed=True)
    clock.now += OPEN_SECONDS - 1

    for call in late:
        call.finish(failed=True)
    clock.now += 1

    assert not is_open(breaker), f"open for {OPEN_SECONDS} s from the first opening"


def test_a_closed_breaker_forgets_the_failures_before_it_opened():
    clock = Clock()
    breaker = make_breaker(clock, window=4, min_calls=2)
    calls(breaker, [True, True])
    clock.now += OPEN_SECONDS
    breaker.acquire().finish(failed=False)

    calls(breaker, [True])

    assert not is_open(breaker), "one failure since it closed"
