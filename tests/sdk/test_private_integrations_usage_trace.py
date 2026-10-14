"""SDK resources for integrations, usage and trace, on both clients.

Shapes follow the engine routes: beakr/api/routes/public_integrations.py and
public_usage_trace.py.
"""

from __future__ import annotations

import json

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


@respx.mock
def test_integrations_resource_calls_each_route(client) -> None:
    listed = respx.get(f"{BASE}/v1/integrations").respond(200, json={"data": []})
    connect = respx.post(f"{BASE}/v1/integrations/slack/connect").respond(
        200, json={"connect_url": "https://connect.example/x"}
    )
    status = respx.get(f"{BASE}/v1/integrations/c1/status").respond(200, json={"id": "c1"})
    coverage = respx.get(f"{BASE}/v1/integrations/c1/coverage").respond(200, json={"known": 3})
    sync = respx.post(f"{BASE}/v1/integrations/c1/sync").respond(202, json={"results": []})
    gone = respx.delete(f"{BASE}/v1/integrations/c1").respond(200, json={"ok": True})

    assert client.integrations.list() == {"data": []}
    assert client.integrations.connect("slack", scope="org")["connect_url"].startswith("https://")
    assert json.loads(connect.calls.last.request.content) == {"scope": "org"}
    assert client.integrations.status("c1")["id"] == "c1"
    assert client.integrations.coverage("c1")["known"] == 3
    client.integrations.sync("c1", project_id="p1")
    assert sync.calls.last.request.url.params["project_id"] == "p1"
    client.integrations.disconnect("c1")
    assert listed.called and status.called and coverage.called and gone.called


@respx.mock
def test_usage_sends_its_range_and_scope(client) -> None:
    route = respx.get(f"{BASE}/v1/usage").respond(200, json={"data": [], "total": {}})
    client.usage.get(group_by="model", start="2026-09-01", end="2026-09-30", scope="org")
    params = route.calls.last.request.url.params
    assert params["group_by"] == "model" and params["scope"] == "org"
    assert params["from"] == "2026-09-01" and params["to"] == "2026-09-30"


@respx.mock
def test_usage_leaves_out_an_unset_range(client) -> None:
    route = respx.get(f"{BASE}/v1/usage").respond(200, json={"data": []})
    client.usage.get()
    assert "from" not in route.calls.last.request.url.params


@respx.mock
def test_trace_addresses_kind_and_id(client) -> None:
    route = respx.get(f"{BASE}/v1/trace/page/p1").respond(200, json={"kind": "page"})
    assert client.trace.get("page", "p1")["kind"] == "page"
    assert route.called


@respx.mock
def test_trace_row_sends_the_card_table_and_row(client) -> None:
    route = respx.get(f"{BASE}/v1/trace/row/card1").respond(200, json={"kind": "row"})
    assert client.trace.row("card1", row=2, table="connections")["kind"] == "row"
    params = route.calls.last.request.url.params
    assert params["table"] == "connections" and params["row"] == "2"
    assert "value" not in params
    client.trace.row("card1", row="Temperature", value="41")
    assert route.calls.last.request.url.params["value"] == "41"


@pytest.mark.asyncio
@respx.mock
async def test_async_trace_row(async_client) -> None:
    route = respx.get(f"{BASE}/v1/trace/row/card1").respond(200, json={"kind": "row"})
    assert (await async_client.trace.row("card1", row="Buffer"))["kind"] == "row"
    assert route.calls.last.request.url.params["row"] == "Buffer"


@pytest.mark.asyncio
@respx.mock
async def test_async_integrations_usage_and_trace(async_client) -> None:
    respx.get(f"{BASE}/v1/integrations").respond(200, json={"data": [{"id": "c1"}]})
    respx.get(f"{BASE}/v1/usage").respond(200, json={"data": [], "total": {"calls": 0}})
    respx.get(f"{BASE}/v1/trace/run/r1").respond(200, json={"kind": "run"})
    assert (await async_client.integrations.list())["data"][0]["id"] == "c1"
    assert (await async_client.usage.get())["total"]["calls"] == 0
    assert (await async_client.trace.get("run", "r1"))["kind"] == "run"
