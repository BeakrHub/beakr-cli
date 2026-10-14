"""The stdio server returns the REST door's Knowledge payload, in the hosted server's shape.

``fixtures/knowledge_door_results.json`` is captured by the engine's
``tests/api/test_knowledge_result_shape_parity.py`` (set
``BEAKR_CAPTURE_KNOWLEDGE_FIXTURES`` to this file's path and run that test to
regenerate). For every Knowledge read command and proposal action it holds the
REST door's request and response, and the hosted MCP server's
``structuredContent`` for the same call -- which that engine test has already
proved is the result Ask's own tool returns, up to the documented transport
differences.

Replaying the REST responses through this server's ``tools/call`` must send the
same request body and build the same result dict as the hosted server (the
REST payload unchanged apart from the shared envelope: text blocks joined, the
public sources, the outcome's status and error code). This server sends that
dict ONCE, rendered as text (``beakr_cli.mcp_render``), with no
``structuredContent``.

ROOT CAUSE pinned by the single-copy assertion: the result used to go out twice,
as a JSON text block and again as ``structuredContent``. Codex printed both
into an exec cell it cut at ~10k tokens, and Claude Code refused 41% of
Knowledge results for exceeding ~50k characters (measured on agent
evaluation traces).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli.mcp_render import render
from beakr_cli.mcp_server import mcp

API = "https://api.example.com"
FIXTURE = Path(__file__).parent / "fixtures" / "knowledge_door_results.json"
CASES: dict[str, Any] = json.loads(FIXTURE.read_text())


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


async def _call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """One ``tools/call`` through the stdio server's MCP request handler."""
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


async def _replay(tool: str, verb: str, path: str, name: str, case: dict[str, Any]) -> None:
    with respx.mock:
        route = respx.post(f"{API}{path}").mock(
            return_value=Response(200, json=case["rest_response"])
        )
        result = await _call_tool(
            tool, {verb: name, "arguments": case["arguments"], "scope": case["scope"]}
        )

    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == case["rest_request"]
    assert not result.isError, result.content
    assert result.structuredContent is None
    assert len(result.content) == 1
    assert result.content[0].text == render(tool, case["mcp"])


def test_the_fixture_covers_every_command_and_action_this_server_documents() -> None:
    from beakr_cli import tool_surface

    read_doc = tool_surface.description("knowledge_base")
    write_doc = tool_surface.description("knowledge_base_write")
    assert CASES["reads"] and CASES["writes"]
    for command in CASES["reads"]:
        assert command in read_doc
    for action in CASES["writes"]:
        assert action in write_doc


@pytest.mark.asyncio
@pytest.mark.parametrize("command", sorted(CASES["reads"]))
async def test_read_returns_the_rest_payload_in_the_hosted_shape(command: str) -> None:
    await _replay(
        "knowledge_base", "command", "/v1/knowledge/wiki/command", command, CASES["reads"][command]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", sorted(CASES["writes"]))
async def test_write_returns_the_rest_payload_in_the_hosted_shape(action: str) -> None:
    await _replay(
        "knowledge_base_write",
        "action",
        "/v1/knowledge/wiki/write",
        action,
        CASES["writes"][action],
    )
