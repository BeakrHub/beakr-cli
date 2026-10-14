"""This server describes its tools with the engine's shared agent tool surface (W8).

Root cause guarded: the stdio server's tool docstrings, the hosted MCP server's
descriptions and Beakr Ask's tool text were three copies that drifted silently.
The engine now keeps one surface (Ask's Knowledge text is its source); this
package vendors it and every tool here takes its description and tier from it.
"""

from __future__ import annotations

import asyncio
import json

from beakr_cli import mcp_server, tool_surface
from tests._engine import engine_checkout


def _listed():
    return asyncio.run(mcp_server.mcp.list_tools())


def test_the_vendored_surface_is_the_engines():
    engine_copy = engine_checkout("docs/api/agent-tool-surface.json")
    assert tool_surface.document() == json.loads(engine_copy.read_text()), (
        "The vendored tool surface is stale: run scripts/sync_tool_surface.py"
    )


def test_every_tool_uses_the_shared_description_and_tier(monkeypatch):
    monkeypatch.setenv(tool_surface.TOOLS_ENV, "all")
    key = tool_surface.document()["tier_meta_key"]
    for tool in _listed():
        assert tool.description == tool_surface.description(tool.name), tool.name
        assert (tool.meta or {}).get(key) == tool_surface.tier(tool.name), tool.name


def test_by_default_this_server_lists_exactly_the_eager_set():
    """W8.2: the small set is the default listing (C042); the rest is discovered."""
    assert [tool.name for tool in _listed()] == tool_surface.eager_tools()


def test_all_mode_lists_exactly_the_stdio_surface(monkeypatch):
    monkeypatch.setenv(tool_surface.TOOLS_ENV, "all")
    assert sorted(tool.name for tool in _listed()) == sorted(tool_surface.stdio_tools())


def test_the_knowledge_tools_are_compact_and_point_at_help_for_asks_full_text():
    # The MCP surfaces carry a compact chooser plus the command index (about
    # 1,000 tokens, down from ~2,600 per session; the engine stack added the
    # verbatim-excerpt and read_file guidance, then the outside-caller defaults
    # and how to cite a source to people and in writes); Ask's full description
    # is one call away as `command: "help"` / `action: "help"` (dogfood row 18).
    read = tool_surface.description("knowledge_base")
    write = tool_surface.description("knowledge_base_write")
    assert "sections: START HERE" in read
    assert "help {topic?}" in read
    assert "What Beakr's own agent is told" not in read + write
    assert len(read) // 4 < 1_050 and len(write) // 4 < 1_050


def test_the_eager_set_is_the_engines_small_set():
    assert tool_surface.document()["eager"] == [
        "context",
        "list_projects",
        "research",
        "knowledge_base",
        "knowledge_base_write",
        "read_file",
        "skills",
        "workboard",
        "discover_tools",
    ]


def test_every_shared_tool_is_on_both_servers():
    """SPEC-493: the stdio and hosted servers expose the same tools, no exceptions.

    beakr_version and update_beakr used to be stdio-only; the hosted server now
    answers them for itself (its version; the local update command).
    """
    for tool in tool_surface.document()["tools"]:
        assert sorted(tool["surfaces"]) == ["remote", "stdio"], tool["name"]
