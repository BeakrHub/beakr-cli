"""The stdio MCP server's paged tools declare the hosted server's bounds and reduce, not refuse.

Root cause: every paged stdio tool took ``limit: int = 50`` with no bounds in
its schema, while the hosted server declares ``{"minimum": 1, "maximum": 50}``.
An agent that learned ``limit=100`` here (silently cut to 50, with nothing in
the reply saying so) was refused on the hosted server, and ``context`` accepted
a ``budget_chars`` below 2000 that the context route answers with 422.
"""

from __future__ import annotations

import json

import anyio
import pytest
import respx
from mcp import ClientSession, types
from mcp.shared.memory import create_client_server_memory_streams

from beakr_cli import __version__, mcp_server
from beakr_cli.mcp_tools_platform import READ_FILE_CHARS

API = "https://api.test"
ORG = {"id": "o1", "name": "Northwind", "slug": "northwind"}
EMPTY_PAGE = {"data": [], "next_cursor": None, "has_more": False}
REDUCED = "Repaired this call: reduced `limit` from 100 to 50, the most rows one page holds"

#: Every stdio tool that takes a row ``limit``; the hosted server gives each ``_LIMIT``.
PAGED_TOOLS = ("list_projects", "skills", "conversations_search", "org_audit_log", "integrations")


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.delenv("BEAKR_AGENT", raising=False)


async def _session_call(name: str, arguments: dict, *, listed: list | None = None):
    """Call a tool through a real MCP session, so the input schema is enforced as a client sees."""
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as group:
            lowlevel = mcp_server.mcp._mcp_server
            group.start_soon(
                lambda: lowlevel.run(*server_streams, mcp_server.mcp.initialization_options())
            )
            info = types.Implementation(name="claude-code", version="2.1.283")
            async with ClientSession(*client_streams, client_info=info) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                if listed is not None:
                    listed.extend((await session.list_tools()).tools)
            group.cancel_scope.cancel()
    return result


def _text(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


def _limit_schema(tool) -> dict:
    schema = tool.inputSchema["properties"]["limit"]
    # An optional limit is declared as anyOf [integer, null].
    return next((s for s in schema.get("anyOf", []) if s.get("type") == "integer"), schema)


async def test_every_paged_tool_declares_the_hosted_limit_bounds() -> None:
    tools = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    for name in PAGED_TOOLS:
        schema = _limit_schema(tools[name])
        assert (schema["minimum"], schema["maximum"]) == (1, 50), name
        assert "at most 50" in schema["description"], name
    # No other tool takes an unbounded row limit.
    unbounded = [
        name
        for name, tool in tools.items()
        if "limit" in tool.inputSchema.get("properties", {})
        and "maximum" not in _limit_schema(tool)
    ]
    assert unbounded == []
    read_file = tools["read_file"].inputSchema["properties"]
    assert (read_file["limit"]["minimum"], read_file["limit"]["maximum"]) == (1, READ_FILE_CHARS)
    assert read_file["offset"]["minimum"] == 0


@respx.mock
async def test_list_projects_over_the_maximum_returns_the_maximum_and_says_so() -> None:
    route = respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": "p1", "name": "Helios", "project_type": "standard"}]}
    )
    respx.get(f"{API}/v1/me/organization").respond(json=ORG)

    result = await _session_call("list_projects", {"limit": 100})

    assert not result.isError, _text(result)
    assert route.calls.last.request.url.params["limit"] == "50"
    assert "Helios" in _text(result)
    assert REDUCED in _text(result)


@respx.mock
async def test_a_limit_within_the_bounds_adds_no_note() -> None:
    route = respx.get(f"{API}/v1/projects").respond(json=EMPTY_PAGE)
    respx.get(f"{API}/v1/me/organization").respond(json=ORG)

    result = await _session_call("list_projects", {"limit": 50})

    assert route.calls.last.request.url.params["limit"] == "50"
    assert "limit reduced" not in _text(result)


@respx.mock
@pytest.mark.parametrize(
    ("tool", "arguments", "path", "sent"),
    [
        ("skills", {"limit": 100}, "/v1/skills", "50"),
        ("conversations_search", {"limit": 100}, "/v1/conversations", "50"),
        # With a query the index search holds 25 a page.
        ("conversations_search", {"query": "pump", "limit": 100}, "/v1/conversations/search", "25"),
        ("org_audit_log", {"limit": 100}, "/v1/organizations/o1/audit", "50"),
        ("integrations", {"command": "list", "limit": 100}, "/v1/integrations/tools", "50"),
    ],
    ids=["skills", "conversations", "conversation-search", "audit", "integrations"],
)
async def test_each_paged_tool_reduces_an_over_maximum_limit_with_a_note(
    tool, arguments, path, sent
) -> None:
    respx.get(f"{API}/v1/me/organization").respond(json=ORG)
    route = respx.get(f"{API}{path}").respond(json=EMPTY_PAGE)

    result = await _session_call(tool, arguments)

    assert not result.isError, _text(result)
    assert route.calls.last.request.url.params["limit"] == sent
    # The hosted server's repair note, word for word, above the result.
    assert _text(result).startswith(
        f"Repaired this call: reduced `limit` from 100 to {sent}, the most rows one page holds; "
        "pass the result's next_cursor as `cursor` for the rows after these."
    )


@respx.mock
async def test_read_file_over_the_maximum_reads_the_largest_window_and_says_so() -> None:
    route = respx.post(f"{API}/v1/artifacts/read").respond(
        json={
            "file": {"id": "f1", "version_id": "v1", "display_path": "memo.md"},
            "text": "Body.",
            "start": 0,
            "end": 5,
            "total_chars": 5,
            "has_more": False,
        }
    )

    result = await _session_call("read_file", {"artifact_id": "f1", "limit": 999_999})

    assert not result.isError, _text(result)
    assert json.loads(route.calls.last.request.content)["max_chars"] == READ_FILE_CHARS
    assert f"limit reduced from 999999 to {READ_FILE_CHARS}" in _text(result)


# ROOT CAUSE (live check 2026-11-13, call 11_input__context_small): the hosted
# server checks a call against the tool's schema and refuses budget_chars=500
# with "500 is less than the minimum of 2000" and the shape to send. This server
# declared the same bounds but raised the budget to 2000 without a word, so the
# caller got a context larger than it asked for and no notice. Both now refuse
# in the same words (the hosted reply of that run, byte for byte).
HOSTED_SMALL_BUDGET_REFUSAL = (
    '{"status": "error", "error_code": "invalid_arguments", "message": "This call does not '
    'fit the context tool: 500 is less than the minimum of 2000.", "fix": "Send the call in '
    'the tool\'s shape: context takes {\\"project\\": <string>, \\"question\\": <string>, '
    '\\"format\\": \\"markdown\\", \\"budget_chars\\": <integer>} (required: none; no other '
    'keys)."}'
)


@respx.mock
async def test_context_refuses_a_budget_under_the_minimum_as_the_hosted_server_does() -> None:
    tools: list = []
    route = respx.post(f"{API}/v1/context").respond(json={"content": "# You"})

    result = await _session_call("context", {"budget_chars": 500}, listed=tools)

    assert result.isError
    assert _text(result) == HOSTED_SMALL_BUDGET_REFUSAL
    assert not route.called
    schema = next(t for t in tools if t.name == "context").inputSchema["properties"]
    assert (schema["budget_chars"]["minimum"], schema["budget_chars"]["maximum"]) == (2000, 20000)


@respx.mock
async def test_context_refuses_a_budget_over_the_maximum() -> None:
    route = respx.post(f"{API}/v1/context").respond(json={"content": "# You"})

    result = await _session_call("context", {"budget_chars": 999_999})

    assert result.isError
    assert "999999 is greater than the maximum of 20000" in _text(result)
    assert not route.called


@respx.mock
@pytest.mark.parametrize("budget", [2000, 20000])
async def test_context_sends_a_budget_at_either_bound(budget: int) -> None:
    route = respx.post(f"{API}/v1/context").respond(json={"content": "# You"})

    result = await _session_call("context", {"budget_chars": budget})

    assert not result.isError, _text(result)
    assert json.loads(route.calls.last.request.content)["budget_chars"] == budget


# The engine tells a caller whose X-Beakr-Client starts with "beakr-mcp" to open
# files with read_file (STDIO_MCP_CLIENT_PREFIX), so a read from this server
# must be named that way, with the MCP client it answers as the agent.
@respx.mock
async def test_a_read_names_this_server_as_the_client_and_the_mcp_client_as_the_agent() -> None:
    route = respx.post(f"{API}/v1/knowledge/documents").respond(
        json={"content": "1  Charter", "sources": {}, "is_error": False}
    )

    result = await _session_call(
        "documents", {"command": "read", "arguments": {"document": "f1"}, "project": ""}
    )

    assert not result.isError, _text(result)
    headers = route.calls.last.request.headers
    assert headers["X-Beakr-Client"] == f"beakr-mcp/{__version__}"
    assert headers["X-Beakr-Agent"] == "claude-code/2.1.283"
