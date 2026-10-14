"""SDK: conversation search and freshness (engine W4.4), on both clients."""

from __future__ import annotations

import pytest
import respx

from .conftest import BASE


# The resources tested here include unlisted routes, reached through the
# private client (``beakr_cli._private_api.PrivateBeakr``) the CLI uses.
@pytest.fixture
def client(internal_client):
    return internal_client


@pytest.fixture
def async_client(async_internal_client):
    return async_internal_client


_HIT = {
    "kind": "imported",
    "id": "c1",
    "title": "Pump calibration",
    "summary": "March curve",
    "findings": [],
    "outputs": [],
    "href": "/v1/conversations/c1",
}


@respx.mock
def test_freshness(client) -> None:
    respx.get(f"{BASE}/v1/conversations/c1/freshness").respond(
        json={"fresh": False, "changed": [{"page_id": "p", "state": "changed"}]}
    )
    assert client.conversations.freshness("c1")["fresh"] is False


@pytest.mark.asyncio
@respx.mock
async def test_async_search_and_freshness(async_client) -> None:
    respx.get(f"{BASE}/v1/conversations/search").respond(json={"data": [_HIT]})
    respx.get(f"{BASE}/v1/conversations/c1/freshness").respond(json={"fresh": True})
    assert (await async_client.conversations.search("pump"))["data"][0]["kind"] == "imported"
    assert (await async_client.conversations.freshness("c1"))["fresh"] is True
