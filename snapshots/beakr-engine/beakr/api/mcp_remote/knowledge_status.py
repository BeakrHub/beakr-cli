"""The Knowledge status notice ``list_projects`` appends for projects that are not settled.

An agent picks a project from ``list_projects`` and then reads its Knowledge. If
that Knowledge is still being built, or some files failed to reach it, an answer
from it can be missing things, and the agent should say so rather than answer as
if the project were complete. The notice names those projects in a line each,
and nothing at all when every listed project is settled.

Reads only what ``GET /v1/projects?include_knowledge=true`` puts on each row
(``knowledge``: queued, running, failed). Self-contained on purpose: the CLI's
stdio MCP server carries a byte-for-byte copy of everything from
``MAX_NOTICE_PROJECTS`` down, so both servers answer the same text
(``beakr-cli`` ``tests/test_mcp_knowledge_status_matches_engine.py``).
"""

from __future__ import annotations

from typing import Any

#: Projects the notice names before summarizing the rest in one line.
MAX_NOTICE_PROJECTS = 5


def knowledge_notice(projects: list[dict[str, Any]]) -> str:
    """Lines for the listed projects whose Knowledge is building or has failures, or ""."""
    unsettled = []
    for project in projects:
        knowledge = project.get("knowledge") or {}
        building = int(knowledge.get("queued") or 0) + int(knowledge.get("running") or 0)
        failed = int(knowledge.get("failed") or 0)
        if building or failed:
            unsettled.append((str(project.get("name") or "Untitled"), building, failed))
    if not unsettled:
        return ""
    lines = ["Knowledge not settled (answers from these projects may be incomplete):"]
    for name, building, failed in unsettled[:MAX_NOTICE_PROJECTS]:
        parts = []
        if building:
            parts.append(f"still building, {_files(building)} queued or running")
        if failed:
            parts.append(f"{_files(failed)} failed and missing until retried")
        lines.append(f"- {name}: {'; '.join(parts)}")
    if len(unsettled) > MAX_NOTICE_PROJECTS:
        lines.append(f"- and {len(unsettled) - MAX_NOTICE_PROJECTS} more projects")
    return "\n".join(lines)


def _files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"
