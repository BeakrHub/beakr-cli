"""A session's read trail: what it read, when and from which projects, and what a write had read.

The API keeps a ledger of a session's reads (``session_read_trail``):
``GET /v1/sessions/{session_id}/trail`` lists them newest first, and
``GET /v1/knowledge/wiki/proposals/{proposal_id}/trail`` gives a write's trail,
the reads its session made before it, grouped by project (the write's own
first, then the others in the order they were first read, then sources outside
Beakr). ``beakr session trail``, ``beakr kb proposals show --trail`` and the
``session_status`` tool print them; both are said here once, for the terminal
and the MCP server alike.
"""

from __future__ import annotations

from typing import Any

#: Reads printed by default; ``beakr session trail --limit`` and the tool take more.
DEFAULT_LIMIT = 50


def trail_path(session_id: str) -> str:
    return f"/v1/sessions/{session_id}/trail"


def proposal_trail_path(proposal_id: str) -> str:
    return f"/v1/knowledge/wiki/proposals/{proposal_id}/trail"


def write_trail_as_person(proposal_id: str) -> str:
    """Why an agent's session is not shown a write's trail, and where a person reads it.

    A write's trail lists what its session read in every project, unredacted for
    its person; shown to an agent it would carry other projects' content into the
    session. The API refuses it a session token (``403 insufficient_scope``), and
    the MCP server never reads it with the person's own credential instead.
    """
    return (
        f"The read trail of write {proposal_id} is read by a person, not by an agent's "
        "session (it can list other projects' reads). In a terminal outside an agent "
        f"conversation: `beakr kb proposals show {proposal_id} --trail`, or open the "
        "proposal in the web app."
    )


def reads_of(page: Any) -> list[dict[str, Any]]:
    """The reads of a trail page (``{data, next_cursor, has_more}``) or a list of them."""
    rows = page.get("data") if isinstance(page, dict) else page
    return [row for row in rows or [] if isinstance(row, dict)]


def _name(project: Any) -> str:
    if isinstance(project, dict):
        return str(project.get("name") or project.get("id") or "?")
    return "?"


def _items(read: dict[str, Any], limit: int = 3) -> str:
    """The first few things the read returned, by title (a redacted one has none)."""
    items = [i for i in read.get("items") or [] if isinstance(i, dict)]
    titles = [
        str(i.get("title") or i.get("service") or i.get("kind") or "?")
        + (f" [{i['service']}]" if i.get("service") and i.get("title") else "")
        for i in items[:limit]
    ]
    more = f" +{len(items) - limit} more" if len(items) > limit else ""
    return ", ".join(titles) + more


def read_line(read: dict[str, Any]) -> str:
    """``2026-11-10 21:04:11  search  Pharma A, Pharma B  "assay results"  (3 items: ...)``."""
    at = str(read.get("at") or "").replace("T", " ")[:19]
    op = str(read.get("op") or "read")
    projects = ", ".join(_name(p) for p in read.get("projects") or []) or "no project"
    parts = [f"{at}  {op:<9} {projects}"]
    summary = str(read.get("summary") or "").strip()
    if summary:
        parts.append(f"  {summary}")
    items = _items(read)
    if items:
        count = len(read.get("items") or [])
        parts.append(f"  ({count} item{'s' if count != 1 else ''}: {items})")
    if read.get("redacted"):
        parts.append("  (redacted: you cannot read all of it)")
    who = read.get("agent") or read.get("client")
    if who:
        parts.append(f"  by {who}")
    return "".join(parts)


def render_trail(page: Any, *, session_id: str | None = None) -> str:
    reads = reads_of(page)
    head = f"Read trail of session {session_id}" if session_id else "Read trail"
    if not reads:
        return f"{head}: nothing read yet."
    lines = [f"{head} (newest first):", *(f"  {read_line(r)}" for r in reads)]
    if isinstance(page, dict) and page.get("has_more"):
        lines.append(f"  ... older reads not shown (next_cursor: {page.get('next_cursor')})")
    return "\n".join(lines)


def render_write_trail(trail: dict[str, Any]) -> str:
    """A write's trail (``WriteTrailOut``): the session's reads before it, by project."""
    write = trail.get("write") if isinstance(trail.get("write"), dict) else {}
    what = f"{write.get('kind') or 'write'} {write.get('id') or ''}".strip()
    project = _name(write.get("project")) if write.get("project") else None
    if not trail.get("recorded"):
        return (
            f"Read trail of {what}: none recorded (written outside a session, or the read "
            "trail was off)."
        )
    total = int(trail.get("reads_total") or 0)
    head = f"Read trail of {what}" + (f" into {project}" if project else "")
    head += f" (session {trail.get('session_id')}): {total} read{'s' if total != 1 else ''}"
    head += " before it"
    if trail.get("truncated"):
        head += ", the first ones shown"
    lines = [head + "."]
    if trail.get("cross_project"):
        lines.append("Cross-project: its session read other projects before writing.")
    for group in trail.get("groups") or []:
        if not isinstance(group, dict):
            continue
        if group.get("external"):
            label = "Outside Beakr"
        elif group.get("project") and project and _name(group["project"]) == project:
            label = f"{project} (this write's project)"
        else:
            label = _name(group.get("project")) + (
                " (another project)" if group.get("cross_project") else ""
            )
        lines.append(f"  {label}:")
        lines.extend(f"    {read_line(r)}" for r in reads_of(group.get("reads")))
    return "\n".join(lines)
