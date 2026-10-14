"""The stdio server lists the eager set and reaches the rest through discovery (W8.2).

Root cause guarded (Codex C042): tier metadata alone did not defer anything; the
server listed all of its tools by default and had no discover_tools.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from beakr_cli import mcp_server, tool_surface

API = "https://api.test"
ARTIFACT = "11111111-2222-3333-4444-555555555555"
VERSION = "99999999-8888-7777-6666-555555555555"
PROJECT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.delenv(tool_surface.TOOLS_ENV, raising=False)


def _text(result) -> str:
    """A tool's answer: one text block, never a second structured copy."""
    assert result.structuredContent is None
    return "\n".join(getattr(item, "text", "") for item in result.content)


async def _shaped(name: str, arguments: dict) -> dict:
    """The tool's own {content, coverage, next, ...} dict (SPEC-488), before rendering."""
    return await mcp_server.mcp._tool_manager.call_tool(name, arguments, convert_result=False)


async def test_the_default_listing_is_the_eager_set_plus_discovery() -> None:
    listed = [tool.name for tool in await mcp_server.mcp.list_tools()]
    assert listed == tool_surface.eager_tools()
    assert "discover_tools" in listed and "accept_proposal" not in listed


async def test_discover_then_call_a_discovered_tool() -> None:
    found = await _shaped("discover_tools", {"search": "stat"})
    names = {tool["name"]: tool for tool in found["tools"]}
    assert "- wiki_stats: " in found["content"]
    assert "wiki_stats" in names
    assert names["wiki_stats"]["input_schema"]["type"] == "object"
    assert not set(names) & set(tool_surface.eager_tools())

    with respx.mock:
        respx.get(f"{API}/v1/knowledge/stats").respond(json={"pages": 7, "sources": 3})
        text = _text(await mcp_server.mcp.call_tool("wiki_stats", {}))
    assert "7" in text


async def test_discovery_lists_every_hidden_tool() -> None:
    found = await _shaped("discover_tools", {})
    every = {tool.name for tool in await FastMCP.list_tools(mcp_server.mcp)}
    hidden = every - set(tool_surface.eager_tools())
    assert {tool["name"] for tool in found["tools"]} == hidden
    assert found["coverage"]["returned"] == len(hidden)


@respx.mock
async def test_workboard_reads_one_project() -> None:
    board = respx.get(f"{API}/v1/knowledge/wiki/work-board").respond(
        json={
            "work": [
                {
                    "agent_kind": "compiler",
                    "project_name": "Assays",
                    "state": "writing",
                    "summary": "Merge kinase cards",
                    "write_refs": ["BK-7"],
                }
            ]
        }
    )
    text = _text(await mcp_server.mcp.call_tool("workboard", {"project": PROJECT}))
    assert "compiler in Assays: writing" in text and "BK-7" in text
    assert board.calls.last.request.url.params["project_id"] == PROJECT
    assert "one scope" in _text(await mcp_server.mcp.call_tool("workboard", {}))


@respx.mock
async def test_read_file_pages_through_a_file() -> None:
    def window(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        start = sent["offset"]
        text = "abcdefghij"[start : start + sent["max_chars"]]
        end = start + len(text)
        return httpx.Response(
            200,
            json={
                "file": {
                    "id": ARTIFACT,
                    "version_id": VERSION,
                    "title": "Assay.pdf",
                    "display_path": "Assay.pdf",
                },
                "unit": None,
                "text": text,
                "start": start,
                "end": end,
                "total_chars": 10,
                "has_more": end < 10,
                "next_offset": end if end < 10 else None,
            },
        )

    respx.post(f"{API}/v1/artifacts/read").mock(side_effect=window)
    first = _text(
        await mcp_server.mcp.call_tool("read_file", {"artifact_id": ARTIFACT, "limit": 4})
    )
    assert "abcd" in first and "offset=4" in first
    rest = _text(
        await mcp_server.mcp.call_tool("read_file", {"artifact_id": ARTIFACT, "offset": 4})
    )
    assert "efghij" in rest and "More text follows" not in rest
