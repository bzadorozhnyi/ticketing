from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from server import ToolContext, mcp, upstreams_of

SEARCH_LIMIT = 100


class EventSummary(BaseModel):
    id: str
    title: str
    starts_at: str
    venue: str
    city: str
    currency: str


class EventsFound(BaseModel):
    events: list[EventSummary]


class Section(BaseModel):
    id: str
    name: str
    seat_count: int
    price_minor: int | None
    currency: str


class EventSections(BaseModel):
    event_id: str
    sections: list[Section]


class Seat(BaseModel):
    id: str
    row: int
    number: int


class SectionSeats(BaseModel):
    event_id: str
    section_id: str
    seats: list[Seat]


@mcp.tool()
async def search_events(ctx: ToolContext, query: str = "") -> EventsFound:
    """Find upcoming events whose title, venue or city contains `query` (case does not
    matter); an empty query lists them all. Returns at most 100 events.
    """
    events = await upstreams_of(ctx).catalog.list_events(limit=SEARCH_LIMIT, offset=0)
    wanted = query.casefold()
    found = [
        EventSummary(
            id=e["id"],
            title=e["title"],
            starts_at=e["starts_at"],
            venue=e["venue"]["name"],
            city=e["venue"]["city"],
            currency=e["currency"],
        )
        for e in events
        if wanted
        in f"{e['title']} {e['venue']['name']} {e['venue']['city']}".casefold()
    ]
    return EventsFound(events=found)


@mcp.tool()
async def get_event_sections(ctx: ToolContext, event_id: str) -> EventSections:
    """The sections of an event's venue with their seat count and price, in minor units
    of the event's currency (15000 = 150.00). A section without a price is not on sale.
    """
    body = await upstreams_of(ctx).catalog.get_sections(event_id=event_id)
    if body is None:
        raise ToolError(f"No event {event_id}. Find events with search_events.")
    return EventSections(
        event_id=event_id,
        sections=[Section(**s, currency=body["currency"]) for s in body["sections"]],
    )


@mcp.tool()
async def get_section_seats(
    ctx: ToolContext, event_id: str, section_id: str
) -> SectionSeats:
    """Every seat of a section by row and number. The map does not show which seats are
    taken: hold_seats tells, and refuses taken seats.
    """
    body = await upstreams_of(ctx).catalog.get_seats(
        event_id=event_id, section_id=section_id
    )
    if body is None:
        raise ToolError(
            f"No section {section_id} for event {event_id}. Use get_event_sections."
        )
    return SectionSeats(
        event_id=event_id,
        section_id=section_id,
        seats=[Seat(**s) for s in body["seats"]],
    )
