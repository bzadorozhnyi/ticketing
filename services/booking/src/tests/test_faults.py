import pytest

from core.faults import crash_point
from core.settings import FaultSettings, settings


class SimulatedCrash(Exception):
    pass


def raise_crash() -> None:
    raise SimulatedCrash


def test_crash_points_are_off_by_default():
    assert settings.FAULTS.CRASH_RATE == 0

    for _ in range(1000):
        crash_point("anywhere", crash=raise_crash)


def test_a_crash_point_kills_at_its_rate():
    with pytest.raises(SimulatedCrash):
        crash_point("anywhere", faults=FaultSettings(CRASH_RATE=1), crash=raise_crash)
