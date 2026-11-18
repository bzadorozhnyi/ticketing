import uuid

from pydantic import BaseModel

from evals.stack import Stack

PRICES = {"Parter": 150000, "Balcony": 80000}


class EventFixture(BaseModel):
    event_id: str
    title: str
    seats: dict[str, list[str]]


def create_event(
    stack: Stack, *, title: str, city: str, sections: dict[str, int]
) -> EventFixture:
    """A venue of its own with the given sections, five seats a row, and an event
    on sale."""
    names = ", ".join(f"($n${name}$n$, {count})" for name, count in sections.items())
    prices = " ".join(
        f"WHEN $n${name}$n$ THEN {price}" for name, price in PRICES.items()
    )
    rows = stack.sql(
        "catalog",
        f"""
        WITH v AS (INSERT INTO venues (name, city)
                   VALUES ($t${title} Hall$t$, $c${city}$c$) RETURNING id),
        e AS (INSERT INTO events (venue_id, title, starts_at, sales_start_at, currency)
              SELECT id, $t${title}$t$, now() + interval '20 days',
                     now() - interval '1 day', 'UAH' FROM v
              RETURNING id),
        wanted (name, seat_count) AS (VALUES {names}),
        s AS (INSERT INTO sections (venue_id, name) SELECT v.id, w.name FROM v, wanted w
              RETURNING id, venue_id, name),
        p AS (INSERT INTO section_prices (event_id, section_id, venue_id, price_minor)
              SELECT e.id, s.id, s.venue_id, CASE s.name {prices} ELSE 50000 END
              FROM e, s RETURNING 1),
        st AS (INSERT INTO seats (section_id, row, number)
               SELECT s.id, (g - 1) / 5 + 1, (g - 1) % 5 + 1
               FROM s JOIN wanted w ON w.name = s.name
               CROSS JOIN LATERAL generate_series(1, w.seat_count) g
               RETURNING id, section_id)
        SELECT e.id, s.name, st.id FROM e, st JOIN s ON s.id = st.section_id
        WHERE (SELECT count(*) FROM p) > 0
    """,
    )
    seats: dict[str, list[str]] = {name: [] for name in sections}
    for _, section, seat_id in rows:
        seats[section].append(seat_id)
    return EventFixture(event_id=rows[0][0], title=title, seats=seats)


def hold_for(
    stack: Stack, *, event: EventFixture, seat_ids: list[str], owner: str
) -> None:
    """Seats someone else holds for 30 minutes, written straight into seat-hold."""
    ids = ",".join(f"'{seat}'" for seat in seat_ids)
    stack.sql(
        "seat-hold",
        f"""
        INSERT INTO holds (event_id, seat_id, hold_id, owner_id, expires_at, status)
        SELECT '{event.event_id}', seat, '{uuid.uuid4()}', '{owner}',
               now() + interval '30 minutes', 'held'
        FROM unnest(ARRAY[{ids}]::uuid[]) AS seat
    """,
    )


def held_by(stack: Stack, user_id: str) -> list[tuple[str, str, str]]:
    """(event, seat, hold) of every seat the user holds or bought right now."""
    rows = stack.sql(
        "seat-hold",
        f"""
        SELECT event_id, seat_id, hold_id FROM holds
        WHERE owner_id = '{user_id}' AND (status = 'confirmed' OR expires_at > now())
    """,
    )
    return [(r[0], r[1], r[2]) for r in rows]


def orders_of(stack: Stack, user_id: str) -> int:
    return int(
        stack.sql(
            "booking", f"SELECT count(*) FROM orders WHERE user_id = '{user_id}'"
        )[0][0]
    )
