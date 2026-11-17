import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from availability.dto.seat_event import SeatChangeDTO, SeatEventDTO, SeatEventType
from availability.models.seat_availability import SeatAvailability
from availability.repositories.seat_availability import SeatAvailabilityRepository
from availability.use_cases.apply_seat_event import ApplySeatEventUseCase

EVENT_ID = uuid.uuid4()
HOLD_ID = uuid.uuid4()
EXPIRES_AT = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def apply(db_session):
    use_case = ApplySeatEventUseCase(
        session=db_session, repository=SeatAvailabilityRepository(session=db_session)
    )

    async def apply(
        event_type: SeatEventType,
        seat_id: uuid.UUID,
        version: int,
        expires_at: datetime | None = None,
    ) -> int:
        event = SeatEventDTO(
            type=event_type,
            event_id=EVENT_ID,
            hold_id=HOLD_ID,
            seats=[
                SeatChangeDTO(seat_id=seat_id, version=version, expires_at=expires_at)
            ],
        )
        return await use_case.execute(event=event)

    return apply


async def stored(db_session, seat_id: uuid.UUID) -> tuple[str, datetime | None, int]:
    row = (
        await db_session.execute(
            select(SeatAvailability).where(
                SeatAvailability.event_id == EVENT_ID,
                SeatAvailability.seat_id == seat_id,
            )
        )
    ).scalar_one()
    await db_session.refresh(row)
    return row.status, row.expires_at, row.version


@pytest.mark.anyio
async def test_a_hold_stores_the_seat_with_its_expiry_and_version(apply, db_session):
    seat = uuid.uuid4()

    changed = await apply(SeatEventType.SEATS_HELD, seat, 5, EXPIRES_AT)

    assert changed == 1
    assert await stored(db_session, seat) == ("held", EXPIRES_AT, 5)


@pytest.mark.anyio
async def test_a_repeated_event_changes_nothing(apply, db_session):
    seat = uuid.uuid4()
    await apply(SeatEventType.SEATS_HELD, seat, 5, EXPIRES_AT)

    changed = await apply(SeatEventType.SEATS_HELD, seat, 5, EXPIRES_AT)

    assert changed == 0
    assert await stored(db_session, seat) == ("held", EXPIRES_AT, 5)


@pytest.mark.anyio
async def test_an_older_event_after_a_release_changes_nothing(apply, db_session):
    seat = uuid.uuid4()
    await apply(SeatEventType.SEATS_HELD, seat, 5, EXPIRES_AT)
    await apply(SeatEventType.SEATS_RELEASED, seat, 9)

    changed = await apply(SeatEventType.SEATS_HELD, seat, 5, EXPIRES_AT)

    assert changed == 0
    assert await stored(db_session, seat) == ("free", None, 9)


@pytest.mark.anyio
async def test_a_release_arriving_first_still_wins(apply, db_session):
    seat = uuid.uuid4()
    await apply(SeatEventType.SEATS_RELEASED, seat, 9)

    await apply(SeatEventType.SEATS_HELD, seat, 5, EXPIRES_AT)

    assert await stored(db_session, seat) == ("free", None, 9)


@pytest.mark.anyio
async def test_extend_then_sell(apply, db_session):
    seat = uuid.uuid4()
    later = EXPIRES_AT + timedelta(minutes=15)
    await apply(SeatEventType.SEATS_HELD, seat, 5, EXPIRES_AT)

    await apply(SeatEventType.HOLD_EXTENDED, seat, 6, later)
    assert await stored(db_session, seat) == ("held", later, 6)

    await apply(SeatEventType.SEATS_SOLD, seat, 7)
    assert await stored(db_session, seat) == ("sold", None, 7)


@pytest.mark.anyio
async def test_seats_of_one_event_change_together(apply, db_session):
    use_case = ApplySeatEventUseCase(
        session=db_session, repository=SeatAvailabilityRepository(session=db_session)
    )
    seats = [uuid.uuid4(), uuid.uuid4()]
    event = SeatEventDTO(
        type=SeatEventType.SEATS_HELD,
        event_id=EVENT_ID,
        hold_id=HOLD_ID,
        seats=[
            SeatChangeDTO(seat_id=seat, version=n, expires_at=EXPIRES_AT)
            for n, seat in enumerate(seats, start=1)
        ],
    )

    assert await use_case.execute(event=event) == 2
    assert [await stored(db_session, seat) for seat in seats] == [
        ("held", EXPIRES_AT, 1),
        ("held", EXPIRES_AT, 2),
    ]
