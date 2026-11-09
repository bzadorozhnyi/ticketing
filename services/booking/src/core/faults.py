import os
import random
import sys
from collections.abc import Callable

from loguru import logger

from core.settings import FaultSettings, settings


def kill_process() -> None:
    """A real crash: no cleanup, no rollback, no `finally`, like `kill -9`."""
    sys.stdout.flush()
    os._exit(1)


def crash_point(
    name: str,
    *,
    faults: FaultSettings = settings.FAULTS,
    crash: Callable[[], None] = kill_process,
) -> None:
    """Kills the process here with the probability in `FAULTS__CRASH_RATE`, for crash
    experiments. Off by default. The name goes to the log first, so a run can count
    how often each point was hit.
    """
    if random.random() < faults.CRASH_RATE:
        logger.bind(crash_point=name).error(f"Crash point {name}")
        crash()
