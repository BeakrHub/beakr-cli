"""A session's read trail: which projects it read, when and how, and what a write had read.

The API keeps a ledger per session (``session_read_trail``): every project a
request read or wrote, in order. ``beakr session trail`` and the
``session_status`` tool print it; ``beakr kb proposals show`` and ``show_proposal``
print a write's own trail, the projects its session had read when it was filed.
Both are said here once, for the terminal and the MCP server alike.
"""

from __future__ import annotations

from typing import Any

#: Where the API keeps a session's trail.
TRAIL_PATH = "/v1/sessions/{session_id}/trail"


def trail_path(session_id: str) -> str:
    return TRAIL_PATH.format(session_id=session_id)


def events_of(page: Any) -> list[dict[str, Any]]:
    """The trail's events from the API's answer (a list, or a page of them)."""
    if isinstance(page, list):
        return [e for e in page if isinstance(e, dict)]
    if isinstance(page, dict):
        for key in ("events", "data", "trail", "items"):
            if isinstance(page.get(key), list):
                return [e for e in page[key] if isinstance(e, dict)]
    return []


def _name(project: Any) -> str:
    if isinstance(project, dict):
        return str(project.get("name") or project.get("id") or "?")
    return str(project or "?")


def event_line(event: dict[str, Any]) -> str:
    """``2026-11-10 21:04  read   Pharma A  knowledge_base sections (search results)``."""
    at = str(event.get("at") or event.get("created_at") or event.get("occurred_at") or "")
    at = at.replace("T", " ")[:19]
    kind = str(event.get("kind") or event.get("action") or "read")
    projects = event.get("projects")
    if isinstance(projects, list) and projects:
        where = ", ".join(_name(p) for p in projects)
    else:
        where = _name(event.get("project"))
    what = str(event.get("what") or event.get("route") or event.get("operation") or "").strip()
    how = str(event.get("via") or event.get("counted_as") or "").replace("_", " ").strip()
    tail = " ".join(part for part in (what, f"({how})" if how else "") if part)
    effect = str(event.get("effect") or event.get("transition") or "").replace("_", " ").strip()
    line = f"{at}  {kind:<6} {where}" + (f"  {tail}" if tail else "")
    return line + (f"  -> {effect}" if effect else "")


def render_trail(page: Any) -> str:
    events = events_of(page)
    if not events:
        return "Read trail: nothing read yet."
    return "\n".join(["Read trail (oldest first):", *(f"  {event_line(e)}" for e in events)])


def write_trail(proposal: dict[str, Any]) -> Any:
    """A write's trail as the proposal carries it; None from an engine without trails."""
    for key in ("read_trail", "session_trail", "trail"):
        if proposal.get(key) is not None:
            return proposal[key]
    return None


def render_write_trail(proposal: dict[str, Any]) -> str:
    trail = write_trail(proposal)
    if trail is None:
        return "This proposal carries no read trail (filed outside a session, or before trails)."
    events = events_of(trail) if not isinstance(trail, dict) or "events" in trail else []
    if isinstance(trail, dict) and not events:
        events = events_of(trail.get("reads"))
    session = trail.get("session_id") if isinstance(trail, dict) else None
    head = "What its session had read when it was filed" + (
        f" (session {session})" if session else ""
    )
    if not events:
        return f"{head}: nothing."
    return "\n".join([f"{head}:", *(f"  {event_line(e)}" for e in events)])
