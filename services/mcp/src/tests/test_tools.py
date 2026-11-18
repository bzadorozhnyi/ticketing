import pytest
from mcp_types import ElicitResult

from tests.conftest import USER
from tests.helpers.services import EVENT, SECTION, TAKEN


def text_of(result) -> str:
    return " ".join(block.text for block in result.content)


@pytest.mark.anyio
async def test_the_tools_say_which_ones_change_something(mcp_client):
    tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}

    assert set(tools) == {
        "search_events",
        "get_event_sections",
        "get_section_seats",
        "hold_seats",
        "release_hold",
        "create_order",
        "get_order",
    }
    assert tools["create_order"].annotations.destructive_hint is True
    assert tools["get_order"].annotations.read_only_hint is True


@pytest.mark.anyio
async def test_search_matches_title_venue_or_city(mcp_client):
    by_city = await mcp_client.call_tool("search_events", {"query": "lviv"})
    everything = await mcp_client.call_tool("search_events", {})

    assert [e["title"] for e in by_city.structured_content["events"]] == [
        "Jazz Evening"
    ]
    assert len(everything.structured_content["events"]) == 20


@pytest.mark.anyio
async def test_search_reads_past_the_first_page(mcp_client):
    found = await mcp_client.call_tool("search_events", {"query": "rock night"})

    assert [e["title"] for e in found.structured_content["events"]] == ["Rock Night"]


@pytest.mark.anyio
async def test_sections_and_seats_of_an_event(mcp_client):
    sections = await mcp_client.call_tool("get_event_sections", {"event_id": EVENT})
    seats = await mcp_client.call_tool(
        "get_section_seats", {"event_id": EVENT, "section_id": SECTION}
    )

    assert sections.structured_content["sections"][0]["price_minor"] == 150000
    assert sections.structured_content["sections"][0]["currency"] == "UAH"
    assert len(seats.structured_content["seats"]) == 2


@pytest.mark.anyio
async def test_an_unknown_event_is_an_error_that_says_what_to_do(mcp_client):
    result = await mcp_client.call_tool("get_event_sections", {"event_id": "nope"})

    assert result.is_error
    assert "search_events" in text_of(result)


@pytest.mark.anyio
async def test_a_hold_is_made_for_the_user_the_gateway_names(mcp_client, services):
    result = await mcp_client.call_tool(
        "hold_seats", {"event_id": EVENT, "seat_ids": ["seat-1"]}
    )

    assert result.structured_content["hold_id"] == "hold-1"
    [call] = services.calls
    assert call["headers"]["X-User-Id"] == USER


@pytest.mark.anyio
async def test_taken_seats_are_named_and_nothing_is_held(mcp_client):
    result = await mcp_client.call_tool(
        "hold_seats", {"event_id": EVENT, "seat_ids": ["seat-1", TAKEN]}
    )

    assert result.is_error
    assert TAKEN in text_of(result)
    assert "Nothing was held" in text_of(result)


@pytest.mark.anyio
async def test_the_user_confirms_and_pays_once_per_hold(mcp_client, services, user):
    first = await mcp_client.call_tool("create_order", {"hold_id": "hold-1"})
    again = await mcp_client.call_tool("create_order", {"hold_id": "hold-1"})

    assert first.structured_content["status"] == "pending"
    assert again.structured_content["order_id"] == first.structured_content["order_id"]
    assert first.structured_content["consistency_token"] == "0/1A2B3C"
    assert len(user.asked) == 2
    keys = [call["headers"]["Idempotency-Key"] for call in services.calls]
    assert keys == ["order:hold-1", "order:hold-1"]
    assert services.calls[0]["body"]["payment_token"] == "tok_ok"


@pytest.mark.anyio
async def test_nothing_is_paid_when_the_user_declines(mcp_client, services, user):
    user.answer = ElicitResult(action="decline")

    result = await mcp_client.call_tool("create_order", {"hold_id": "hold-1"})

    assert result.is_error
    assert "did not confirm" in text_of(result)
    assert services.calls == []


@pytest.mark.anyio
async def test_an_order_is_read_with_its_consistency_token(mcp_client, services):
    result = await mcp_client.call_tool(
        "get_order", {"order_id": "order-1", "consistency_token": "0/1A2B3C"}
    )

    assert result.structured_content["status"] == "confirmed"
    assert services.calls[0]["headers"]["X-Consistency-Token"] == "0/1A2B3C"


@pytest.mark.anyio
async def test_without_the_gateway_s_user_nothing_happens(anonymous_client, services):
    result = await anonymous_client.call_tool(
        "hold_seats", {"event_id": EVENT, "seat_ids": ["seat-1"]}
    )

    assert result.is_error
    assert "Not signed in" in text_of(result)
    assert services.calls == []


@pytest.mark.anyio
async def test_health_and_ready_answer_without_a_user():
    import httpx2

    from main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://mcp:8000"
        ) as http,
    ):
        assert (await http.get("/health")).status_code == 200
        assert (await http.get("/ready")).status_code == 200
