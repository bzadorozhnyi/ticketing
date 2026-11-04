import io

import pytest
from loguru import logger

from core.logging import configure_logging
from core.settings import LogFormat, settings


@pytest.fixture
def json_output(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(settings.LOGGING, "FORMAT", LogFormat.JSON)
    configure_logging()
    yield output
    monkeypatch.undo()
    configure_logging()


def test_json_traceback_does_not_expose_variable_values(json_output):
    card_number = "4242424242424242"
    try:
        len(card_number) / 0
    except ZeroDivisionError:
        logger.exception("Charge failed")

    log = json_output.getvalue()
    assert "ZeroDivisionError" in log
    assert card_number not in log
