import factory
import pytest

from events.models.section_price import SectionPrice
from tests.helpers.factory import AsyncFactory


@pytest.fixture
def section_price_factory(db_session, event_factory, section_factory):
    class SectionPriceFactory(factory.alchemy.SQLAlchemyModelFactory):
        class Meta:
            model = SectionPrice
            sqlalchemy_session = db_session.sync_session
            sqlalchemy_session_persistence = "flush"
            exclude = ("event", "section")

        event = factory.SubFactory(event_factory.factory_class)
        section = factory.SubFactory(
            section_factory.factory_class, venue=factory.SelfAttribute("..event.venue")
        )
        event_id = factory.LazyAttribute(lambda o: o.event.id)
        section_id = factory.LazyAttribute(lambda o: o.section.id)
        venue_id = factory.LazyAttribute(lambda o: o.event.venue_id)
        price_minor = 150000

    return AsyncFactory(factory_class=SectionPriceFactory, session=db_session)
