import pytest
from mcp.server.request_state import InvalidRequestState, RequestStateSecurity
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitResult, InputRequiredResult, InputResponses

from server import request_state_security
from tests.conftest import connect


def test_a_state_sealed_on_one_pod_opens_on_another():
    first_pod, second_pod = request_state_security(), request_state_security()

    sealed = first_pod.codec.seal(b"payment question")

    assert second_pod.codec.unseal(sealed) == b"payment question"


def test_the_sdk_default_key_works_on_one_pod_only():
    first_pod, second_pod = (
        RequestStateSecurity.ephemeral(),
        RequestStateSecurity.ephemeral(),
    )

    sealed = first_pod.codec.seal(b"payment question")

    with pytest.raises(InvalidRequestState):
        second_pod.codec.unseal(sealed)


@pytest.mark.anyio
async def test_another_user_cannot_answer_a_users_payment_question(services, user):
    async for owner in connect({"x-user-id": "user-1"}, user):
        asked = await owner.session.call_tool(
            "create_order", {"hold_id": "hold-1"}, allow_input_required=True
        )
    assert isinstance(asked, InputRequiredResult)
    assert asked.input_requests is not None
    [(key, _)] = asked.input_requests.items()
    answer: InputResponses = {
        key: ElicitResult(action="accept", content={"payment_token": "tok_ok"})
    }

    async for stranger in connect({"x-user-id": "user-2"}, user):
        with pytest.raises(MCPError):
            await stranger.session.call_tool(
                "create_order",
                {"hold_id": "hold-1"},
                input_responses=answer,
                request_state=asked.request_state,
                allow_input_required=True,
            )

    assert services.calls == []
