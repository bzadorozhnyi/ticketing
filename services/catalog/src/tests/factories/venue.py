import factory
import pytest

from tests.helpers.factory import AsyncFactory
from venues.models.venue import Venue


@pytest.fixture
def venue_factory(db_session):
    class VenueFactory(factory.alchemy.SQLAlchemyModelFactory):
        class Meta:
            model = Venue
            sqlalchemy_session = db_session.sync_session
            sqlalchemy_session_persistence = "flush"

        name = factory.Sequence(lambda n: f"Venue {n}")
        city = factory.Faker("city")

    return AsyncFactory(factory_class=VenueFactory, session=db_session)
