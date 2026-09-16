"""The skill and slash commands must describe the tools this server actually has.

Regression for the 0.2.1 -> 0.3.0 drift: the MCP server replaced 25 ``kb_*``
tools with ``knowledge_base`` / ``knowledge_base_write``, but the skill and
slash commands that ``beakr setup`` copies into Claude Code and Codex kept
telling agents to call ``kb_search``, ``kb_cat`` and ``kb_propose_create`` --
tools that no longer existed. Nothing checked the bundled Markdown against the
server, so every agent following the skill would have failed its first call.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from beakr_cli import mcp_server

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET_FILES = sorted(
    [*(REPO_ROOT / ".claude" / "skills" / "beakr").rglob("*.md"),
     *(REPO_ROOT / ".claude" / "commands").glob("kb-*.md")]
)


def _tool_names() -> set[str]:
    return {
        name
        for name, obj in vars(mcp_server).items()
        if inspect.iscoroutinefunction(obj) and not name.startswith("_")
    }


def _documented(docstring: str, pattern: str) -> set[str]:
    return set(re.findall(pattern, docstring, flags=re.MULTILINE))


#: A `name {args}` entry in a tool description: indented, or set off by 2+ spaces.
_ENTRY = r"(?:^\s+|\s{2,})([a-z_]+)\s+\{"
#: Commands listed in knowledge_base's own description.
READ_COMMANDS = _documented(inspect.getdoc(mcp_server.knowledge_base) or "", _ENTRY)
#: Actions listed in knowledge_base_write's description.
WRITE_ACTIONS = _documented(inspect.getdoc(mcp_server.knowledge_base_write) or "", _ENTRY)


def test_asset_files_are_found() -> None:
    assert len(ASSET_FILES) >= 12


def test_no_asset_names_a_removed_per_operation_tool() -> None:
    offenders = {
        f"{path.relative_to(REPO_ROOT)}: {match}"
        for path in ASSET_FILES
        for match in re.findall(r"\bkb_[a-z_]+", path.read_text())
    }
    assert not offenders, f"skill/commands reference removed kb_* tools: {sorted(offenders)}"


def test_every_tool_call_in_the_assets_exists() -> None:
    tools = _tool_names()
    offenders = {
        f"{path.relative_to(REPO_ROOT)}: {name}"
        for path in ASSET_FILES
        for name in re.findall(r"\b([a-z]+(?:_[a-z]+)+)\(", path.read_text())
        if name not in tools
    }
    assert not offenders, f"skill/commands call tools the server does not expose: {offenders}"


def test_every_command_and_action_in_the_assets_is_documented_on_the_tool() -> None:
    assert "search" not in READ_COMMANDS, "there is no `search` read command"
    offenders = set()
    for path in ASSET_FILES:
        text = path.read_text()
        for command in re.findall(r'command="([a-z_]+)"', text):
            if command not in READ_COMMANDS:
                offenders.add(f"{path.name}: command={command}")
        for action in re.findall(r'action="([a-z_]+)"', text):
            if action not in WRITE_ACTIONS:
                offenders.add(f"{path.name}: action={action}")
    assert not offenders, sorted(offenders)


def test_tool_descriptions_cover_the_core_vocabulary() -> None:
    """Agents cannot discover argument names any other way; keep the core listed."""
    assert {"sections", "grep", "ls", "cat", "provenance", "sources", "timeline"} <= READ_COMMANDS
    assert {"edit_section", "new", "edit", "mv", "archive", "merge"} <= WRITE_ACTIONS
