"""``beakr changes``: the activity feed (who changed what, newest first).

It reads the organization's feed, or one project's. ``beakr watch`` (the event
stream) and ``beakr audit export`` are not in this release.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import typer

from beakr_cli.output import (
    emit,
    err_console,
    print_table,
    short,
)
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_RELATIVE = re.compile(r"^(\d+)([mhdw])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """``7d``, ``12h``, ``30m``, ``2w``, or an ISO date/time (UTC when no zone)."""
    match = _RELATIVE.match(value.strip())
    if match:
        delta = timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
        return (now or datetime.now(timezone.utc)) - delta
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise typer.BadParameter(
            "Use a duration like 7d, 12h or 30m, or an ISO date like 2026-10-01.",
            param_hint="--since",
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def changes(
    since: str | None = typer.Option(
        None, "--since", "-s", help="Only changes after this: 7d, 12h, or an ISO date (default 7d)."
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Only this project (default: the whole organization).",
        callback=project_ref_callback,
    ),
    agent: str | None = typer.Option(
        None, "--agent", "-a", help="Only changes this agent made (its X-Beakr-Agent name)."
    ),
    user: str | None = typer.Option(
        None, "--user", "-u", help="Only changes this person made (user id)."
    ),
    kind: list[str] = typer.Option(  # noqa: B008 - typer reads the default
        [],
        "--kind",
        "-k",
        help="knowledge.commit, proposal.filed or file.added; repeatable.",
    ),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=1000, help="Most changes to show."),
    json_out: bool = _json_opt,
) -> None:
    """What changed, newest first: Knowledge changes, proposals and files, and who made each.

    Filtering happens on the server (``GET /v1/changes``), by time, project,
    person and agent.
    """
    cutoff = parse_since(since) if since else None
    project_id = resolve_project(project) if project else None
    events: list[dict[str, Any]] = []
    with get_sdk() as sdk:
        cursor: str | None = None
        while len(events) < limit:
            page = sdk.knowledge.changes(
                since=cutoff.isoformat() if cutoff else None,
                project_id=project_id,
                user_id=user,
                agent=agent,
                kind=list(kind) or None,
                limit=min(200, limit - len(events)),
                cursor=cursor,
            )
            events.extend(page.get("data") or [])
            cursor = page.get("next_cursor")
            if not cursor:
                break

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No changes" + (f" since {since}." if since else "."), markup=False)
            return
        print_table(
            ["When", "Who", "What", "Summary"],
            [
                [
                    str(e.get("created_at") or "")[:16],
                    short(e.get("agent") or e.get("actor_user_id") or "-", 24),
                    short(e.get("kind"), 18),
                    short(e.get("summary"), 50),
                ]
                for e in rows
            ],
            title=f"Changes ({len(rows)})",
        )

    emit(events[:limit], json_out=json_out, render=render)


def register(root: typer.Typer) -> None:
    """Attach ``beakr changes``."""
    root.command("changes")(changes)
