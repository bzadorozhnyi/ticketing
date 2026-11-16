import uuid

import pytest
from fastapi import status

pytestmark = pytest.mark.anyio

URL = "/internal/v1/orders/existing"


async def test_answers_which_orders_exist(async_client, order_factory):
    first, second = await order_factory(), await order_factory(user_id="user-2")
    unknown = uuid.uuid4()

    response = await async_client.post(
        URL, json={"order_ids": [str(first.id), str(unknown), str(second.id)]}
    )

    assert response.status_code == status.HTTP_200_OK
    assert set(response.json()["order_ids"]) == {str(first.id), str(second.id)}


async def test_none_found_is_an_empty_list(async_client):
    response = await async_client.post(URL, json={"order_ids": [str(uuid.uuid4())]})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"order_ids": []}


@pytest.mark.parametrize("count", [0, 1001])
async def test_the_batch_is_bounded(async_client, count):
    order_ids = [str(uuid.uuid4()) for _ in range(count)]

    response = await async_client.post(URL, json={"order_ids": order_ids})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
