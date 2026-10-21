"""How the stdio server lists its tools, and how the rest are reached (W8.2).

Root causes guarded:

- Codex C042: tier metadata alone did not defer anything; the server had no
  discover_tools.
- Claude Code calls only the tools a server lists, so when the default listing
  was the eager set, the tools discover_tools described (documents,
  wiki_stats, the proposal tools...) could not be called from it at all. Every
  tool is now listed by default; the eager mode (``BEAKR_MCP_TOOLS=eager``)
  adds what discover_tools finds to the listing and sends tools/list_changed.
- A self-hosted installation leaves out hosted features (skills, integrations,
  organization administration) and answers a bare 404; their tools are no
  longer listed there.
"""

from __future__ import annotations

import json

import anyio
import httpx
import pytest
import respx
from beakr_sdk.capabilities import forget_capabilities
from mcp import ClientSession, types
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_client_server_memory_streams

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


@pytest.fixture(autouse=True)
def fresh_listing():
    """The server is one process-wide object; each test starts from its first listing."""
    mcp_server.mcp._revealed.clear()
    mcp_server.mcp._hidden.clear()
    yield
    mcp_server.mcp._revealed.clear()
    mcp_server.mcp._hidden.clear()


def _text(result) -> str:
    """A tool's answer: one text block, never a second structured copy."""
    assert result.structuredContent is None
    return "\n".join(getattr(item, "text", "") for item in result.content)


async def _shaped(name: str, arguments: dict) -> dict:
    """The tool's own {content, coverage, next, ...} dict (SPEC-488), before rendering."""
    return await mcp_server.mcp._tool_manager.call_tool(name, arguments, convert_result=False)


async def test_the_default_listing_is_every_tool() -> None:
    listed = [tool.name for tool in await mcp_server.mcp.list_tools()]
    assert sorted(listed) == sorted(tool_surface.stdio_tools())
    assert {"documents", "wiki_stats", "accept_proposal", "beakr_version"} <= set(listed)
    assert tool_surface.DISCOVERY_NOTE not in (mcp_server.mcp.instructions or "")


async def test_the_eager_mode_lists_the_eager_set_plus_discovery(monkeypatch) -> None:
    monkeypatch.setenv(tool_surface.TOOLS_ENV, "eager")
    assert tool_surface.discovery_note() == tool_surface.DISCOVERY_NOTE
    listed = [tool.name for tool in await mcp_server.mcp.list_tools()]
    assert listed == tool_surface.eager_tools()
    assert "discover_tools" in listed and "accept_proposal" not in listed


def test_the_server_declares_that_its_tool_listing_changes() -> None:
    options = mcp_server.mcp.initialization_options()
    assert options.capabilities.tools is not None
    assert options.capabilities.tools.listChanged is True


async def _session(handler):
    """A client connected to the real server, with ``handler`` reading its notifications."""
    streams = create_client_server_memory_streams()
    (client_read, client_write), (server_read, server_write) = await streams.__aenter__()
    group = anyio.create_task_group()
    await group.__aenter__()
    lowlevel = mcp_server.mcp._mcp_server
    group.start_soon(
        lambda: lowlevel.run(server_read, server_write, mcp_server.mcp.initialization_options())
    )
    session = ClientSession(client_read, client_write, message_handler=handler)
    await session.__aenter__()
    initialized = await session.initialize()

    async def close() -> None:
        await session.__aexit__(None, None, None)
        group.cancel_scope.cancel()
        await group.__aexit__(None, None, None)
        await streams.__aexit__(None, None, None)

    return session, initialized, close


async def test_eager_mode_discovery_adds_the_found_tools_and_says_so(monkeypatch) -> None:
    monkeypatch.setenv(tool_surface.TOOLS_ENV, "eager")
    changes: list[str] = []

    async def handler(message) -> None:
        if isinstance(message, types.ServerNotification) and isinstance(
            message.root, types.ToolListChangedNotification
        ):
            changes.append(message.root.method)

    session, initialized, close = await _session(handler)
    try:
        assert initialized.capabilities.tools.listChanged is True
        before = {tool.name for tool in (await session.list_tools()).tools}
        assert "wiki_stats" not in before

        await session.call_tool("discover_tools", {"search": "stat"})
        after = {tool.name for tool in (await session.list_tools()).tools}
        assert "wiki_stats" in after and before < after
        assert changes == ["notifications/tools/list_changed"]

        # Finding the same tools again changes nothing.
        await session.call_tool("discover_tools", {"search": "stat"})
        assert changes == ["notifications/tools/list_changed"]
    finally:
        await close()


async def test_the_default_mode_discovery_changes_no_listing() -> None:
    before = {tool.name for tool in await mcp_server.mcp.list_tools()}
    await _shaped("discover_tools", {"search": "stat"})
    assert {tool.name for tool in await mcp_server.mcp.list_tools()} == before
    assert mcp_server.mcp._revealed == set()


@pytest.mark.real_capabilities
async def test_tools_the_installation_does_not_serve_are_not_listed() -> None:
    # A box's own document: no conversations, so their two tools are left out.
    document = {
        "paths": {
            path: {"get": {}, "post": {}}
            for path in (
                "/v1/projects",
                "/v1/context",
                "/v1/knowledge/research",
                "/v1/knowledge/wiki/command",
                "/v1/knowledge/wiki/write",
                "/v1/knowledge/documents",
                "/v1/artifacts/read",
                "/v1/knowledge/wiki/proposals/{proposal_id}",
                "/v1/knowledge/wiki/proposals/{proposal_id}/accept",
                "/v1/knowledge/wiki/proposals/{proposal_id}/dismiss",
                "/v1/public/openapi.json",
            )
        }
    }
    with respx.mock:
        respx.get(f"{API}/v1/public/openapi.json").respond(json=document)
        listed = {tool.name for tool in await mcp_server.mcp.list_tools()}
    assert "conversations_search" not in listed and "import_conversation" not in listed
    # Skills are documented (GET /v1/skills), and a box's document leaves them out.
    assert "skills" not in listed
    assert {"research", "knowledge_base", "documents"} <= listed


@pytest.mark.real_capabilities
async def test_the_instructions_name_only_tools_the_installation_lists(monkeypatch) -> None:
    # Root cause: the instructions always sent agents to a 'skills' tool, which
    # a box does not list. They are rebuilt before `initialize` is answered.
    monkeypatch.setattr(mcp_server.mcp._mcp_server, "instructions", mcp_server.mcp.instructions)
    box = {"paths": {"/v1/projects": {"get": {}}, "/v1/context": {"post": {}}}}
    with respx.mock:
        respx.get(f"{API}/v1/public/openapi.json").respond(json=box)
        await mcp_server.mcp.fit_instructions()
    on_a_box = mcp_server.mcp.instructions or ""
    assert "'skills'" not in on_a_box and "SKILL.md" not in on_a_box
    assert "'context' once at the start of a task for the user's profiles and memories." in (
        on_a_box
    )

    forget_capabilities()  # the SDK caches the document per base URL
    hosted = {"paths": {**box["paths"], "/v1/skills": {"get": {}}}}
    with respx.mock:
        respx.get(f"{API}/v1/public/openapi.json").respond(json=hosted)
        await mcp_server.mcp.fit_instructions()
    assert "'skills' to read a SKILL.md before following it." in (mcp_server.mcp.instructions or "")


@pytest.mark.real_capabilities
async def test_a_server_without_a_document_lists_every_tool() -> None:
    with respx.mock:
        respx.get(f"{API}/v1/public/openapi.json").respond(404, json={"detail": "Not Found"})
        listed = {tool.name for tool in await mcp_server.mcp.list_tools()}
    assert listed == set(tool_surface.stdio_tools())


async def test_a_route_the_installation_lacks_says_so_and_leaves_the_listing() -> None:
    """skills on a box: its route is not served, so the call says so and the tool goes."""
    changes: list[str] = []

    async def handler(message) -> None:
        if isinstance(message, types.ServerNotification):
            changes.append(message.root.method)

    with respx.mock:
        respx.get(f"{API}/v1/skills").respond(404, json={"detail": "Not Found"})
        session, _, close = await _session(handler)
        try:
            result = await session.call_tool("skills", {})
            listed = {tool.name for tool in (await session.list_tools()).tools}
        finally:
            await close()
    text = result.content[0].text
    assert result.isError
    assert "This Beakr installation does not provide skills." in text
    assert "not_provided" in text
    assert "skills" not in listed
    assert changes == ["notifications/tools/list_changed"]


async def test_a_missing_record_is_still_an_ordinary_not_found() -> None:
    with respx.mock:
        respx.get(f"{API}/v1/knowledge/wiki/proposals/p1").respond(
            404,
            json={
                "detail": "Proposal not found",
                "error": {"code": "not_found", "message": "Proposal not found"},
            },
        )
        result = await mcp_server.mcp.call_tool("show_proposal", {"proposal_id": "p1"})
    assert result.isError and "Proposal not found" in result.content[0].text
    assert "show_proposal" in {tool.name for tool in await mcp_server.mcp.list_tools()}


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
