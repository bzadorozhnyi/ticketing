import subprocess
import sys

import pytest

from tests.conftest import ALEMBIC_INI_PATH


@pytest.mark.anyio
async def test_models_match_migrations(async_engine):
    """Runs in a fresh interpreter: this process has already imported every
    model through the app, which would hide a module missing from models_registry.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI_PATH), "check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
