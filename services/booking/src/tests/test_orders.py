import re
import uuid

import pytest
from fastapi import status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core import consistency
from core.consistency import TOKEN_HEADER, TOKEN_PATTERN
from core.db import get_replica_session
from main import app
from orders.models.order import FailureReason, Order, OrderStatus, SagaStep
from orders.models.payment_token import OrderPaymentToken
from orders.use_cases.create_order import request_fingerprint

pytestmark = pytest.mark.anyio

ORDERS_URL = "/api/v1/orders"
USER = "user-1"


def headers(key: str | None = "key-1", user: str | None = USER) -> dict[str, str]:
    values = {"Idempotency-Key": key, "X-User-Id": user}
    return {name: value for name, value in values.items() if value is not None}


def order_body(hold_id: uuid.UUID, token: str = "tok_ok") -> dict[str, str]:
    return {"hold_id": str(hold_id), "payment_token": token}


async def count_orders(session) -> int:
    return await session.scalar(select(func.count()).select_from(Order))


async def test_create_stores_a_pending_order(async_client, db_session):
    hold_id = uuid.uuid4()

    response = await async_client.post(
        ORDERS_URL, json=order_body(hold_id), headers=headers()
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    body = response.json()
    assert set(body) == {"id", "hold_id", "status", "failure_reason", "created_at"}
    assert body["hold_id"] == str(hold_id)
    assert body["status"] == OrderStatus.PENDING

    order = await db_session.get(Order, uuid.UUID(body["id"]))
    assert order.user_id == USER
    assert order.idempotency_key == "key-1"
    token = await db_session.get(OrderPaymentToken, order.id)
    assert token.payment_token == "tok_ok"
    column = await db_session.scalar(
        text("SELECT payment_token FROM orders WHERE id = :id"), {"id": order.id}
    )
    assert column is None
    assert order.request_fingerprint == request_fingerprint(
        hold_id=hold_id, payment_token="tok_ok"
    )


async def test_order_keeps_the_trace_of_the_request_for_its_saga(
    async_client, db_session
):
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    traced = {**headers(), "traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"}

    response = await async_client.post(
        ORDERS_URL, json=order_body(uuid.uuid4()), headers=traced
    )

    order = await db_session.get(Order, uuid.UUID(response.json()["id"]))
    assert order.traceparent is not None
    assert order.traceparent.split("-")[1] == trace_id


async def test_repeat_with_the_same_key_returns_the_first_order(
    async_client, db_session
):
    hold_id = uuid.uuid4()

    first = await async_client.post(
        ORDERS_URL, json=order_body(hold_id), headers=headers()
    )
    repeat = await async_client.post(
        ORDERS_URL, json=order_body(hold_id), headers=headers()
    )

    assert repeat.status_code == status.HTTP_202_ACCEPTED
    assert repeat.json() == first.json()
    assert await count_orders(db_session) == 1
    tokens = await db_session.scalar(
        select(func.count()).select_from(OrderPaymentToken)
    )
    assert tokens == 1


@pytest.mark.parametrize("order_status", list(OrderStatus))
async def test_repeat_returns_the_order_in_its_current_status(
    async_client, order_factory, order_status
):
    order = await order_factory(idempotency_key="key-1", status=order_status)

    response = await async_client.post(
        ORDERS_URL, json=order_body(order.hold_id), headers=headers()
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json()["id"] == str(order.id)
    assert response.json()["status"] == order_status


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("tok_ok", status.HTTP_202_ACCEPTED),
        ("tok_another", status.HTTP_422_UNPROCESSABLE_CONTENT),
    ],
)
async def test_a_repeat_of_a_final_order_is_checked_without_its_token(
    async_client, order_factory, token, expected
):
    """A final order keeps no token: its fingerprint says whether the repeat is the
    same request."""
    order = await order_factory(
        idempotency_key="key-1", status=OrderStatus.CONFIRMED, saga_step=SagaStep.DONE
    )

    response = await async_client.post(
        ORDERS_URL, json=order_body(order.hold_id, token), headers=headers()
    )

    assert response.status_code == expected


@pytest.mark.parametrize(
    ("same_hold", "token"), [(False, "tok_ok"), (True, "tok_another")]
)
async def test_same_key_with_another_request_is_refused(
    async_client, db_session, order_factory, same_hold, token
):
    order = await order_factory(idempotency_key="key-1")
    hold_id = order.hold_id if same_hold else uuid.uuid4()

    response = await async_client.post(
        ORDERS_URL, json=order_body(hold_id, token), headers=headers()
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["code"] == "validation_error"
    assert await count_orders(db_session) == 1


async def test_a_key_belongs_to_its_user(async_client, db_session, order_factory):
    order = await order_factory(idempotency_key="key-1", user_id="user-2")

    response = await async_client.post(
        ORDERS_URL, json=order_body(uuid.uuid4()), headers=headers()
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json()["id"] != str(order.id)
    assert await count_orders(db_session) == 2


@pytest.mark.parametrize("order_status", [OrderStatus.PENDING, OrderStatus.CONFIRMED])
async def test_active_order_blocks_another_on_the_same_hold(
    async_client, db_session, order_factory, order_status
):
    order = await order_factory(status=order_status)

    response = await async_client.post(
        ORDERS_URL, json=order_body(order.hold_id), headers=headers()
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["code"] == "conflict"
    assert await count_orders(db_session) == 1


async def test_failed_order_leaves_the_hold_free(
    async_client, db_session, order_factory
):
    order = await order_factory(status=OrderStatus.FAILED)

    response = await async_client.post(
        ORDERS_URL, json=order_body(order.hold_id, "tok_another"), headers=headers()
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json()["id"] != str(order.id)
    assert response.json()["status"] == OrderStatus.PENDING
    assert await count_orders(db_session) == 2


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (None, status.HTTP_422_UNPROCESSABLE_CONTENT),
        ("", status.HTTP_422_UNPROCESSABLE_CONTENT),
        ("k" * 256, status.HTTP_422_UNPROCESSABLE_CONTENT),
        ("k" * 255, status.HTTP_202_ACCEPTED),
    ],
)
async def test_idempotency_key_is_required_and_bounded(async_client, key, expected):
    response = await async_client.post(
        ORDERS_URL, json=order_body(uuid.uuid4()), headers=headers(key=key)
    )

    assert response.status_code == expected


@pytest.mark.parametrize("token", ["", "t" * 256])
async def test_payment_token_is_bounded(async_client, token):
    response = await async_client.post(
        ORDERS_URL, json=order_body(uuid.uuid4(), token), headers=headers()
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.parametrize("user", [None, ""])
async def test_create_without_a_user_is_refused(async_client, db_session, user):
    response = await async_client.post(
        ORDERS_URL, json=order_body(uuid.uuid4()), headers=headers(user=user)
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["code"] == "unauthorized"
    assert await count_orders(db_session) == 0


@pytest.fixture
def lagging_replica(async_engine):
    """Another connection does not see the test's uncommitted order, as a lagging
    replica does not see the latest commit yet."""

    async def replica_session():
        async with AsyncSession(async_engine) as session:
            yield session

    app.dependency_overrides[get_replica_session] = replica_session


@pytest.fixture
async def unreachable_replica():
    engine = create_async_engine(
        "postgresql+asyncpg://postgres:password@127.0.0.1:1/booking_db",
        connect_args={"timeout": 1},
    )

    async def replica_session():
        async with AsyncSession(engine) as session:
            yield session

    app.dependency_overrides[get_replica_session] = replica_session
    yield
    await engine.dispose()


async def get_order(client, order_id, token: str | None = None):
    headers = {"X-User-Id": USER}
    if token is not None:
        headers[TOKEN_HEADER] = token
    return await client.get(f"{ORDERS_URL}/{order_id}", headers=headers)


async def test_create_returns_a_consistency_token(async_client):
    response = await async_client.post(
        ORDERS_URL, json=order_body(uuid.uuid4()), headers=headers()
    )

    assert re.fullmatch(TOKEN_PATTERN, response.headers[TOKEN_HEADER])


async def test_an_order_the_replica_lacks_is_read_from_the_primary(
    async_client, order_factory, lagging_replica
):
    order = await order_factory()

    response = await get_order(async_client, order.id)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["id"] == str(order.id)


async def test_with_a_token_a_replica_behind_is_skipped(
    async_client, order_factory, lagging_replica, monkeypatch
):
    async def behind(session, token):
        return False

    monkeypatch.setattr(consistency, "replica_has", behind)
    order = await order_factory()

    response = await get_order(async_client, order.id, token="0/1")

    assert response.status_code == status.HTTP_200_OK


async def test_with_a_token_a_replica_that_has_it_answers_alone(
    async_client, order_factory, lagging_replica
):
    """Once the replica has the client's write, its answer stands: a 404 is not read
    again from the primary."""
    order = await order_factory()

    response = await get_order(async_client, order.id, token="0/1")

    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_an_unreachable_replica_sends_reads_to_the_primary(
    async_client, order_factory, unreachable_replica
):
    order = await order_factory()

    response = await get_order(async_client, order.id, token="0/1")

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.parametrize("token", ["0/1; DROP TABLE orders", "x/y", "0-1", ""])
async def test_a_malformed_token_is_refused(async_client, order_factory, token):
    order = await order_factory()

    response = await get_order(async_client, order.id, token=token)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_get_returns_the_order(async_client, order_factory):
    order = await order_factory(status=OrderStatus.CONFIRMED)

    response = await async_client.get(
        f"{ORDERS_URL}/{order.id}", headers={"X-User-Id": USER}
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert set(body) == {"id", "hold_id", "status", "failure_reason", "created_at"}
    assert body["id"] == str(order.id)
    assert body["hold_id"] == str(order.hold_id)
    assert body["status"] == OrderStatus.CONFIRMED


async def test_get_explains_a_failed_order(async_client, order_factory):
    order = await order_factory(
        status=OrderStatus.FAILED, failure_reason=FailureReason.CARD_DECLINED
    )

    response = await async_client.get(
        f"{ORDERS_URL}/{order.id}", headers={"X-User-Id": USER}
    )

    assert response.json()["status"] == OrderStatus.FAILED
    assert response.json()["failure_reason"] == FailureReason.CARD_DECLINED


async def test_get_shows_why_a_pending_order_is_being_refunded(
    async_client, order_factory
):
    order = await order_factory(
        saga_step="awaiting_refund", failure_reason=FailureReason.CHARGE_TIMED_OUT
    )

    response = await async_client.get(
        f"{ORDERS_URL}/{order.id}", headers={"X-User-Id": USER}
    )

    assert response.json()["status"] == OrderStatus.PENDING
    assert response.json()["failure_reason"] == FailureReason.CHARGE_TIMED_OUT


async def test_another_users_order_looks_missing(async_client, order_factory):
    order = await order_factory(user_id="user-2")

    response = await async_client.get(
        f"{ORDERS_URL}/{order.id}", headers={"X-User-Id": USER}
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["code"] == "not_found"


async def test_unknown_order_is_not_found(async_client):
    response = await async_client.get(
        f"{ORDERS_URL}/{uuid.uuid4()}", headers={"X-User-Id": USER}
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_get_without_a_user_is_refused(async_client, order_factory):
    order = await order_factory()

    response = await async_client.get(f"{ORDERS_URL}/{order.id}")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_database_refuses_an_unknown_saga_step(db_session, order_factory):
    with pytest.raises(IntegrityError, match="ck_orders_saga_step_known"):
        await order_factory(saga_step="lost")
