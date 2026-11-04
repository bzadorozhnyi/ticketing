import pytest
from fastapi import status

from core.schemas import ErrorOut
from main import app
from scripts.export_openapi import CONTRACT_PATH, render_contract


def test_contract_file_matches_code():
    assert CONTRACT_PATH.read_text() == render_contract(), (
        "Stale contract: run `pre-commit run contract --all-files`"
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/health", {status.HTTP_200_OK}),
        ("/ready", {status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE}),
    ],
)
def test_documented_statuses(path, expected):
    responses = app.openapi()["paths"][path]["get"]["responses"]
    assert set(responses) == {str(code) for code in expected}


@pytest.mark.anyio
async def test_error_response_matches_documented_schema(async_client):
    response = await async_client.get("/no-such-route")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    ErrorOut.model_validate(response.json())
