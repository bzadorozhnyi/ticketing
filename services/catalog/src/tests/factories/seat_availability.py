from datetime import UTC, datetime, timedelta

import factory
import pytest

from availability.models.seat_availability import SeatAvailability
from tests.helpers.factory import AsyncFactory


@pytest.fixture
def seat_availability_factory(db_session):
    class SeatAvailabilityFactory(factory.alchemy.SQLAlchemyModelFactory):
        class Meta:
            model = SeatAvailability
            sqlalchemy_session = db_session.sync_session
            sqlalchemy_session_persistence = "flush"
            exclude = ("event", "seat")

        event_id = factory.LazyAttribute(lambda o: o.event.id)
        seat_id = factory.LazyAttribute(lambda o: o.seat.id)
        status = "held"
        expires_at = factory.LazyFunction(
            lambda: datetime.now(UTC) + timedelta(minutes=5)
        )
        version = factory.Sequence(lambda n: n + 1)

    return AsyncFactory(factory_class=SeatAvailabilityFactory, session=db_session)
