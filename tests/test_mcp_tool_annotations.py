"""Every tool `beakr mcp` lists carries a title and honest MCP annotations.

Root cause guarded: CLI 0.6.0 listed knowledge_base, list_projects, documents,
read_file, research and nearly every other tool with ``annotations: None``.
Codex treats an un-annotated MCP tool as possibly side-effecting: in a read-only
sandbox it asked for approval on every Knowledge read, and under
``approval_policy="never"`` (any ``codex exec``) it refused the call ("the tool
requires approval, but approval policy is never"). The hints are the engine's
(vendored in the shared tool surface), so both MCP servers list the same ones.

The fixed lists below are the decision record: a tool added to the surface
fails here until it is placed in one of them.
"""

from __future__ import annotations

import asyncio

import pytest

from beakr_cli import mcp_server, tool_surface

#: Tools that change nothing.
READ_TOOLS = frozenset(
    {
        "context",
        "list_projects",
        "knowledge_base",
        "read_file",
        "research",
        "skills",
        "workboard",
        "discover_tools",
        "documents",
        "get_profile",
        "wiki_stats",
        "wiki_graph",
        "show_proposal",
        "conversations_search",
        "org_audit_log",
        "integrations",
        "upload_status",
        "beakr_version",
        # The self-hosted line's session tools (engine #2930's hints).
        "session_status",
    }
)
#: Tools that change something. update_beakr upgrades this installation here (the
#: hosted server only returns the command, so it is read-only there).
WRITE_TOOLS = frozenset(
    {
        "knowledge_base_write",
        "accept_proposal",
        "dismiss_proposal",
        "import_conversation",
        "create_project",
        "set_project_profile",
        "upload_files",
        "add_project_member",
        "update_beakr",
        "start_session",
    }
)
#: Reads that reach a connected service, or PyPI.
OPEN_WORLD_READS = frozenset({"research", "integrations", "beakr_version"})

_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


@pytest.fixture
def listed(monkeypatch):
    monkeypatch.setenv(tool_surface.TOOLS_ENV, "all")
    return {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}


def test_every_tool_is_decided():
    assert set(tool_surface.stdio_tools()) == READ_TOOLS | WRITE_TOOLS
    assert not READ_TOOLS & WRITE_TOOLS


def test_every_listed_tool_has_a_title_and_all_four_hints(listed):
    assert set(listed) == READ_TOOLS | WRITE_TOOLS
    for name, tool in listed.items():
        assert tool.title == tool_surface.title(name), name
        assert tool.annotations is not None, name
        assert tool.annotations.title == tool.title, name
        for hint in _HINTS:
            assert isinstance(getattr(tool.annotations, hint), bool), f"{name}.{hint}"


def test_reads_are_read_only_and_writes_are_not(listed):
    for name in READ_TOOLS:
        hints = listed[name].annotations
        assert hints.readOnlyHint is True, name
        assert hints.destructiveHint is False, name
        assert hints.idempotentHint is True, name
        assert hints.openWorldHint is (name in OPEN_WORLD_READS), name
    for name in WRITE_TOOLS:
        assert listed[name].annotations.readOnlyHint is False, name


def test_the_knowledge_write_door_and_the_upgrade_are_marked_destructive(listed):
    # knowledge_base_write also answers `help`, but one name that writes gets the write
    # hints; edit/edit_section replace text and auto-approve applies them at once.
    write = listed["knowledge_base_write"].annotations
    assert (write.destructiveHint, write.idempotentHint) == (True, False)
    update = listed["update_beakr"].annotations
    assert (update.destructiveHint, update.idempotentHint, update.openWorldHint) == (
        True,
        True,
        True,
    )


def test_start_session_is_a_write_that_neither_deletes_nor_reaches_out(listed):
    # Declaring a session's projects changes it, the same way each time, inside Beakr.
    hints = listed["start_session"].annotations
    assert (hints.readOnlyHint, hints.destructiveHint) == (False, False)
    assert (hints.idempotentHint, hints.openWorldHint) == (True, False)
    assert listed["start_session"].title == "Start this session"
    assert listed["session_status"].title == "Session status"


def test_the_hints_are_the_engines_with_this_servers_overrides():
    for tool in tool_surface.document()["tools"]:
        expected = (tool.get("surface_annotations") or {}).get("stdio") or tool["annotations"]
        given = tool_surface.annotations(tool["name"]).model_dump(include=set(_HINTS))
        assert given == expected, tool["name"]


def test_discover_tools_reports_read_only_from_the_hints(monkeypatch):
    monkeypatch.setenv(tool_surface.TOOLS_ENV, "all")
    found = asyncio.run(mcp_server.discover_tools())
    read_only = {tool["name"]: tool["read_only"] for tool in found["tools"]}
    assert read_only["documents"] is True
    assert read_only["wiki_stats"] is True
    assert read_only["accept_proposal"] is False
