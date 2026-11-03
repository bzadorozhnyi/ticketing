from datetime import UTC, datetime, timedelta

import factory
import pytest

from events.models.event import Event
from tests.helpers.factory import AsyncFactory


@pytest.fixture
def event_factory(db_session, venue_factory):
    class EventFactory(factory.alchemy.SQLAlchemyModelFactory):
        class Meta:
            model = Event
            sqlalchemy_session = db_session.sync_session
            sqlalchemy_session_persistence = "flush"

        venue = factory.SubFactory(venue_factory.factory_class)
        title = factory.Sequence(lambda n: f"Event {n}")
        starts_at = factory.LazyFunction(lambda: datetime.now(UTC) + timedelta(days=30))
        sales_start_at = factory.LazyFunction(
            lambda: datetime.now(UTC) + timedelta(days=1)
        )

    return AsyncFactory(factory_class=EventFactory, session=db_session)
