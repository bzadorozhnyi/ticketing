import uuid

import factory
import pytest

from payments.models.charge import Charge, ChargeStatus
from tests.helpers.factory import AsyncFactory


@pytest.fixture
def charge_factory(db_session):
    class ChargeFactory(factory.alchemy.SQLAlchemyModelFactory):
        class Meta:
            model = Charge
            sqlalchemy_session = db_session.sync_session
            sqlalchemy_session_persistence = "flush"

        order_id = factory.LazyFunction(uuid.uuid4)
        amount_minor = 10_000
        currency = "UAH"
        status = ChargeStatus.SUCCEEDED

    return AsyncFactory(factory_class=ChargeFactory, session=db_session)
