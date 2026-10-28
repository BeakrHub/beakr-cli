"""The stdio ``integrations`` tool: connected-integration reads over the public API.

An agent on this server could not read the user's Slack, Drive, Gmail or Jira:
those reads existed only as ``beakr integrations tools|call`` and the SDK.
The tool calls the same two routes through the SDK (``GET /v1/integrations/tools``,
``POST /v1/integrations/tools/{tool_name}/call``), which decide what a call may
reach; these tests fake the API and check what is sent and what the agent reads.
The engine's parity tests hold this server's answers equal to the hosted one's.
"""

from __future__ import annotations

import json

import pytest
import respx
from beakr_sdk.capabilities import forget_capabilities

from beakr_cli import __version__, mcp_server, tool_surface
from beakr_cli.mcp_integration_results import FIXES

API = "https://api.test"
CONNECTOR = "11111111-2222-3333-4444-555555555555"

SLACK = {
    "name": "slack_list_messages",
    "provider": "slack:nango",
    "connections": [{"connector_id": CONNECTOR, "label": "Lab Slack"}],
    "description": "Get recent messages from a Slack channel.\n\nUSE FOR: browsing.",
    "input_schema": {
        "type": "object",
        "properties": {"channel_id": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["channel_id"],
    },
}
CALLED = {
    "tool": "slack_list_messages",
    "provider": "slack:nango",
    "connector_ids": [CONNECTOR],
    "content": "Assay plate 7 is done {{slack:ab12cd34}}",
    "is_error": False,
    "sources": [
        {
            "source_ref": "beakr-source:v1:key:c2xhY2s6YWIxMmNkMzQ",
            "citation_key": "slack:ab12cd34",
            "name": "#assays message",
            "location": {"url": "https://slack.example/archives/C1/p1"},
        }
    ],
}


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.delenv(tool_surface.TOOLS_ENV, raising=False)


@pytest.fixture(autouse=True)
def fresh_listing():
    mcp_server.mcp._hidden.clear()
    yield
    mcp_server.mcp._hidden.clear()


def _page(rows: list[dict], cursor: str | None = None) -> dict:
    return {"data": rows, "has_more": cursor is not None, "next_cursor": cursor}


async def _call(arguments: dict):
    result = await mcp_server.mcp.call_tool("integrations", arguments)
    assert result.structuredContent is None
    return result, "\n".join(block.text for block in result.content)


@respx.mock
async def test_list_reads_one_page_and_names_each_tools_options() -> None:
    route = respx.get(f"{API}/v1/integrations/tools").respond(json=_page([SLACK], "next"))
    result, text = await _call({"command": "list", "provider": "slack", "limit": 500})
    assert not result.isError
    request = route.calls.last.request
    assert request.headers["X-Beakr-Client"] == f"beakr-mcp/{__version__}"
    assert dict(request.url.params) == {"provider": "slack", "limit": "50"}
    assert f"slack:nango -- Lab Slack (connector_id {CONNECTOR})" in text
    assert "- slack_list_messages {channel_id, limit?}: Get recent messages" in text
    assert "USE FOR" not in text
    assert 'call integrations again with cursor="next"' in text


@respx.mock
async def test_describe_gives_one_tools_description_and_schema() -> None:
    route = respx.get(f"{API}/v1/integrations/tools").mock(
        side_effect=[
            respx.MockResponse(json=_page([{**SLACK, "name": "slack_get_user"}], "c2")),
            respx.MockResponse(json=_page([SLACK])),
        ]
    )
    _, text = await _call({"command": "describe", "tool": "slack_list_messages"})
    assert [call.request.url.params.get("cursor") for call in route.calls] == [None, "c2"]
    assert "USE FOR: browsing." in text
    schema = json.loads(text.split("input_schema: ", 1)[1].split("\n", 1)[0])
    assert schema == SLACK["input_schema"]


@respx.mock
async def test_call_sends_the_tools_arguments_and_lists_its_sources() -> None:
    route = respx.post(f"{API}/v1/integrations/tools/slack_list_messages/call").respond(json=CALLED)
    result, text = await _call(
        {
            "command": "call",
            "tool": "slack_list_messages",
            "arguments": {"channel_id": "C1", "limit": 5},
            "connector_id": CONNECTOR,
        }
    )
    assert not result.isError
    assert json.loads(route.calls.last.request.content) == {
        "arguments": {"channel_id": "C1", "limit": 5},
        "connector_id": CONNECTOR,
    }
    assert text.startswith("Assay plate 7 is done {{slack:ab12cd34}}")
    assert (
        "- {{slack:ab12cd34}} #assays message · https://slack.example/archives/C1/p1 "
        "· source_ref beakr-source:v1:key:c2xhY2s6YWIxMmNkMzQ"
    ) in text


@respx.mock
async def test_a_tools_options_beside_tool_are_folded_into_arguments() -> None:
    route = respx.post(f"{API}/v1/integrations/tools/slack_list_messages/call").respond(json=CALLED)
    result, _ = await _call({"command": "call", "tool": "slack_list_messages", "channel_id": "C1"})
    assert not result.isError
    assert json.loads(route.calls.last.request.content) == {"arguments": {"channel_id": "C1"}}


@respx.mock
async def test_a_list_option_beside_a_call_is_refused_and_nothing_is_sent() -> None:
    # The tool's own limit belongs in `arguments`; a top-level one would be dropped.
    route = respx.post(f"{API}/v1/integrations/tools/slack_list_messages/call").respond(json=CALLED)
    result, text = await _call({"command": "call", "tool": "slack_list_messages", "limit": 5})
    assert result.isError and not route.called
    error = json.loads(text)
    assert error["error_code"] == "invalid_arguments"
    assert "`limit` is for command='list' only" in error["message"]


@respx.mock
async def test_a_refusal_keeps_the_routes_message_with_a_fix_in_this_tools_terms() -> None:
    respx.post(f"{API}/v1/integrations/tools/slack_post_message/call").respond(
        403,
        json={
            "error": {
                "code": "tool_not_read_only",
                "message": "slack_post_message changes data in the connected account.",
                "fix": "Call a read-only tool from `GET /v1/integrations/tools`.",
            }
        },
    )
    result, text = await _call({"command": "call", "tool": "slack_post_message"})
    assert result.isError
    error = json.loads(text)
    assert error["error_code"] == "tool_not_read_only"
    assert error["message"] == "slack_post_message changes data in the connected account."
    assert error["fix"] == FIXES["tool_not_read_only"]


@respx.mock
async def test_a_failure_the_tool_reports_is_an_error_result() -> None:
    respx.post(f"{API}/v1/integrations/tools/slack_list_messages/call").respond(
        json={**CALLED, "is_error": True, "content": "C2 is not within the allowed scope."}
    )
    result, text = await _call({"command": "call", "tool": "slack_list_messages"})
    assert result.isError
    error = json.loads(text)
    assert error["error_code"] == "tool_reported_error"
    assert error["message"] == "C2 is not within the allowed scope."


@respx.mock
async def test_on_a_box_without_integrations_the_tool_says_so_and_leaves_the_listing() -> None:
    respx.get(f"{API}/v1/integrations/tools").respond(404, json={"detail": "Not Found"})
    result, text = await _call({"command": "list"})
    assert result.isError and "not_provided" in text
    assert "integrations" not in {tool.name for tool in await mcp_server.mcp.list_tools()}


@pytest.mark.real_capabilities
async def test_the_instructions_name_integrations_only_where_it_is_listed(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server.mcp._mcp_server, "instructions", mcp_server.mcp.instructions)
    box = {"paths": {"/v1/projects": {"get": {}}, "/v1/context": {"post": {}}}}
    with respx.mock:
        respx.get(f"{API}/v1/public/openapi.json").respond(json=box)
        listed = {tool.name for tool in await mcp_server.mcp.list_tools()}
        await mcp_server.mcp.fit_instructions()
    assert "integrations" not in listed
    assert "'integrations'" not in (mcp_server.mcp.instructions or "")

    forget_capabilities()  # the SDK caches the document per base URL
    hosted = {
        "paths": {
            **box["paths"],
            "/v1/integrations/tools": {"get": {}},
            "/v1/integrations/tools/{tool_name}/call": {"post": {}},
        }
    }
    with respx.mock:
        respx.get(f"{API}/v1/public/openapi.json").respond(json=hosted)
        listed = {tool.name for tool in await mcp_server.mcp.list_tools()}
        await mcp_server.mcp.fit_instructions()
    assert "integrations" in listed
    assert "'integrations' reads one connected service" in (mcp_server.mcp.instructions or "")
