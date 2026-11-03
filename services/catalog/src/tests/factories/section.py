import factory
import pytest

from tests.helpers.factory import AsyncFactory
from venues.models.section import Section


@pytest.fixture
def section_factory(db_session, venue_factory):
    class SectionFactory(factory.alchemy.SQLAlchemyModelFactory):
        class Meta:
            model = Section
            sqlalchemy_session = db_session.sync_session
            sqlalchemy_session_persistence = "flush"

        venue = factory.SubFactory(venue_factory.factory_class)
        name = factory.Sequence(lambda n: f"Section {n}")

    return AsyncFactory(factory_class=SectionFactory, session=db_session)
