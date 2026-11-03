import factory
import pytest

from tests.helpers.factory import AsyncFactory
from venues.models.seat import Seat


@pytest.fixture
def seat_factory(db_session, section_factory):
    class SeatFactory(factory.alchemy.SQLAlchemyModelFactory):
        class Meta:
            model = Seat
            sqlalchemy_session = db_session.sync_session
            sqlalchemy_session_persistence = "flush"

        section = factory.SubFactory(section_factory.factory_class)
        row = 1
        number = factory.Sequence(lambda n: n + 1)

    return AsyncFactory(factory_class=SeatFactory, session=db_session)
