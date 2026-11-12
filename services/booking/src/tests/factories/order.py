import uuid

import factory
import pytest

from orders.models.order import Order, OrderStatus
from orders.use_cases.create_order import request_fingerprint
from tests.helpers.factory import AsyncFactory


@pytest.fixture
def order_factory(db_session):
    class OrderFactory(factory.alchemy.SQLAlchemyModelFactory):
        class Meta:
            model = Order
            sqlalchemy_session = db_session.sync_session
            sqlalchemy_session_persistence = "flush"

        user_id = "user-1"
        idempotency_key = factory.LazyFunction(lambda: str(uuid.uuid4()))
        hold_id = factory.LazyFunction(uuid.uuid4)
        request_fingerprint = factory.LazyAttribute(
            lambda order: request_fingerprint(
                hold_id=order.hold_id, payment_token=order.payment_token
            )
        )
        status = OrderStatus.PENDING

        class Params:
            payment_token = "tok_ok"

    return AsyncFactory(factory_class=OrderFactory, session=db_session)
