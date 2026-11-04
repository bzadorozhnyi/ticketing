import uuid

import pytest
from fastapi import status

from core.schemas import ErrorOut
from main import app
from scripts.export_openapi import CONTRACT_PATH, render_contract

OK = status.HTTP_200_OK
NOT_FOUND = status.HTTP_404_NOT_FOUND
INVALID = status.HTTP_422_UNPROCESSABLE_CONTENT
UNAVAILABLE = status.HTTP_503_SERVICE_UNAVAILABLE


def test_contract_file_matches_code():
    assert CONTRACT_PATH.read_text() == render_contract(), (
        "Stale contract: run `pre-commit run catalog-contract --all-files`"
    )


def test_default_validation_error_is_not_documented():
    assert "HTTPValidationError" not in app.openapi()["components"]["schemas"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1/events", {OK, INVALID, UNAVAILABLE}),
        ("/api/v1/events/{event_id}", {OK, NOT_FOUND, INVALID, UNAVAILABLE}),
        ("/api/v1/events/{event_id}/sections", {OK, NOT_FOUND, INVALID, UNAVAILABLE}),
        (
            "/api/v1/events/{event_id}/sections/{section_id}/seats",
            {OK, NOT_FOUND, INVALID, UNAVAILABLE},
        ),
        ("/health", {OK}),
        ("/ready", {OK, UNAVAILABLE}),
    ],
)
def test_documented_statuses(path, expected):
    responses = app.openapi()["paths"][path]["get"]["responses"]
    assert set(responses) == {str(code) for code in expected}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"/api/v1/events/{uuid.uuid4()}", NOT_FOUND),
        ("/api/v1/events?limit=0", INVALID),
    ],
    ids=["not-found", "invalid"],
)
async def test_error_response_matches_documented_schema(async_client, url, expected):
    response = await async_client.get(url)
    assert response.status_code == expected
    ErrorOut.model_validate(response.json())
