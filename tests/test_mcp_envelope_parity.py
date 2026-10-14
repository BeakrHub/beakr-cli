"""The stdio ``beakr mcp`` server answers a tool call in the hosted MCP's envelope.

Both servers send each result once, as one text block, with no
``structuredContent``, and an error as compact JSON ``{status, error_code,
message, fix}`` marked ``isError`` (beakr-engine ``beakr/api/mcp_remote/server.py``
``call_tool`` and ``render.py``). Root cause this guards: the stdio server
declared ``structured_output`` and sent every Knowledge result twice, so clients
printed both copies; and its error results lacked ``status``. These tests run
the stdio server's real ``tools/call`` handler against a fake engine response
and build the hosted envelope for the same tool result with the engine's own
``render`` module, read from the engine checkout.
"""

from __future__ import annotations

import importlib.util
import json
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli.mcp_server import mcp
from tests._engine import engine_checkout

API = "https://api.example.com"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


@pytest.fixture(scope="module")
def hosted_render():
    """The hosted server's renderer, loaded from the engine checkout."""
    path = engine_checkout("beakr/api/mcp_remote/render.py")
    spec = importlib.util.spec_from_file_location("_hosted_mcp_render", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _stdio_call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


async def _tool_body(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The tool's own result dict, before either server wraps it in an envelope."""
    return await mcp._tool_manager.call_tool(name, arguments, convert_result=False)


def _hosted_success(render, name: str, body: dict[str, Any]) -> types.CallToolResult:
    # beakr/api/mcp_remote/server.py call_tool, success branch.
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=render.render(name, body))]
    )


def _hosted_error(text: str) -> types.CallToolResult:
    # beakr/api/mcp_remote/server.py _error_result.
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)


def _envelope(result: types.CallToolResult) -> dict[str, Any]:
    return result.model_dump(exclude_none=True, by_alias=True)


def _door_reply(text: str, *, status: str = "success", code: str | None = None) -> dict:
    outcome: dict[str, Any] = {"status": status}
    if code:
        outcome["error_code"] = code
    return {
        "content": [{"type": "text", "text": text}],
        "outcome": outcome,
        "sources": [
            {
                "source_ref": "src-1@v1",
                "citation_key": "s1",
                "filename": "assay.pdf",
                "provider": "upload",
                "excerpt": "Kd was 38 nM in triplicate.",
                "location": {"file_id": "11111111-1111-1111-1111-111111111111"},
            }
        ],
        "coverage": {"command": "sections", "status": status, "returned": 1, "total": 1},
        "next": [],
    }


@pytest.mark.asyncio
@respx.mock
async def test_a_knowledge_result_is_one_text_block_rendered_as_the_hosted_server_does(
    hosted_render,
) -> None:
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=Response(200, json=_door_reply("Kd: 38 nM {{s1}}"))
    )
    arguments = {"command": "sections", "arguments": {"query": "Kd"}}

    stdio = await _stdio_call("knowledge_base", arguments)
    body = await _tool_body("knowledge_base", arguments)

    assert _envelope(stdio) == _envelope(_hosted_success(hosted_render, "knowledge_base", body))
    assert "structuredContent" not in _envelope(stdio)
    assert len(stdio.content) == 1 and not stdio.isError
    assert "assay.pdf" in stdio.content[0].text  # the Sources list, rendered once


@pytest.mark.asyncio
@respx.mock
async def test_an_error_outcome_is_the_hosted_compact_error(hosted_render) -> None:
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=Response(
            200, json=_door_reply("No page named Nowhere.", status="error", code="page_not_found")
        )
    )
    arguments = {"command": "cat", "arguments": {"page": "Nowhere"}}

    stdio = await _stdio_call("knowledge_base", arguments)
    body = await _tool_body("knowledge_base", arguments)

    assert _envelope(stdio) == _envelope(_hosted_error(hosted_render.body_error_text(body)))
    error = json.loads(stdio.content[0].text)
    assert error["status"] == "error" and error["error_code"] == "page_not_found"


@pytest.mark.asyncio
@respx.mock
async def test_an_api_refusal_is_the_hosted_compact_error(hosted_render) -> None:
    respx.get(f"{API}/v1/projects").mock(
        return_value=Response(
            403,
            json={
                "error": {
                    "code": "insufficient_scope",
                    "message": "This credential lacks the 'projects:read' scope.",
                    "fix": "Use a key with projects:read.",
                }
            },
        )
    )

    stdio = await _stdio_call("list_projects", {})

    hosted = _hosted_error(
        hosted_render.error_text(
            message="This credential lacks the 'projects:read' scope.",
            error_code="insufficient_scope",
            fix="Use a key with projects:read.",
        )
    )
    assert _envelope(stdio) == _envelope(hosted)
    assert json.loads(stdio.content[0].text) == {
        "status": "error",
        "error_code": "insufficient_scope",
        "message": "This credential lacks the 'projects:read' scope.",
        "fix": "Use a key with projects:read.",
    }


@pytest.mark.asyncio
async def test_no_tool_declares_an_output_schema(monkeypatch) -> None:
    # A declared outputSchema obliges every result to repeat itself as
    # structuredContent (MCP spec), which is what doubled the results.
    monkeypatch.setenv("BEAKR_MCP_TOOLS", "all")
    tools = await mcp._mcp_server.request_handlers[types.ListToolsRequest](
        types.ListToolsRequest(method="tools/list")
    )
    assert [t.name for t in tools.root.tools if t.outputSchema] == []


def test_a_compact_records_sources_footer_matches_the_hosted_one(hosted_render) -> None:
    # Root cause (proof run B2): compact Knowledge records carry their tokens
    # and passages in ``excerpts`` and only ``location.file_id``; both servers
    # read only ``evidence.excerpt``, so the footer listed no token, no excerpt
    # and (once location was dropped) no read_file id. The engine fixed its
    # renderer; this pins the stdio copy to it.
    from beakr_cli.mcp_render import render

    body = {
        "content": "Kd: 38 nM {{s1}} {{s2}}",
        "sources": [
            {
                "filename": "assay.pdf",
                "provider": "upload",
                "location": {"file_id": "11111111-1111-1111-1111-111111111111"},
                "excerpts": [
                    {"citation_key": "s1", "text": "Kd was 38 nM in triplicate."},
                    {"citation_key": "s2", "text": "Replicates agreed within 5%."},
                ],
            }
        ],
    }
    stdio = render("knowledge_base", body)
    assert stdio == hosted_render.render("knowledge_base", body)
    assert "{{s1}}" in stdio and "{{s2}}" in stdio
    assert "read_file 11111111-1111-1111-1111-111111111111" in stdio
    assert "Kd was 38 nM in triplicate." in stdio


def test_sources_lines_lead_with_the_path_and_each_tokens_place(hosted_render) -> None:
    # Each passage says where it sits (``unit_locator``) and each document where it
    # lives (``display_path``), so the footer shows ``path: {{token}} › p. 7`` --
    # ``path#p. 7`` is what read_file opens. The stdio copy must match the hosted
    # renderer byte for byte, compact excerpts included.
    from beakr_sdk.sources import public_source

    from beakr_cli.mcp_render import render

    raw = {
        "name": "memo.pdf",
        "citation_key": "artifact:v1-u1",
        "provider": "upload",
        "location": {
            "file_id": "11111111-1111-1111-1111-111111111111",
            "display_path": "deals/acme/memo.pdf",
            "unit_locator": "p. 7",
        },
        "excerpts": [
            {"citation_key": "artifact:v1-u1", "unit_locator": "p. 7", "text": "Rent is $5."},
            {"citation_key": "artifact:v1-u2", "unit_locator": "p. 9", "text": "Term: 5 years."},
        ],
        "cited_passages": 2,
    }
    body = {"content": "Rent {{artifact:v1-u1}}", "sources": [public_source(raw)]}
    stdio = render("knowledge_base", body)
    assert stdio == hosted_render.render("knowledge_base", body)
    assert (
        "- deals/acme/memo.pdf: {{artifact:v1-u1}} › p. 7 {{artifact:v1-u2}} › p. 9 · "
        "read_file 11111111-1111-1111-1111-111111111111 · 2 passages cited"
    ) in stdio
    assert '  "Rent is $5."' in stdio
