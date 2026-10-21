"""``beakr kb entities`` and ``beakr kb history`` on the ``kb`` Typer app.

``entities`` lists Knowledge pages by type (people, organizations, matters and
whatever else the project's ontology types its pages as). ``history`` is the
spec's name for ``kb log``: a page's revisions, or recent ingestion globally.
"""

from __future__ import annotations

from collections import Counter
from itertools import islice
from typing import Any

import typer

from beakr_cli.commands.kb import app as kb_app
from beakr_cli.commands.kb import log
from beakr_cli.compact import compact_lines
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk


def entities(
    page_type: str | None = typer.Option(
        None, "--type", "-t", help="Only this type, e.g. person or organization."
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        "-s",
        help="Only this project.",
        callback=project_ref_callback,
    ),
    limit: int = typer.Option(200, "--limit", "-n", min=1, max=1000, help="Most pages to list."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Knowledge entities (pages) by type, with a count per type."""
    project_id = resolve_project(project) if project else None
    with get_sdk() as sdk:
        pages: list[dict[str, Any]] = list(
            islice(
                sdk.knowledge.pages.iter(
                    project_id=project_id,
                    page_type=page_type,
                    sort_by="title",
                    page_size=min(limit, 200),
                ),
                limit,
            )
        )

    truncated = len(pages) >= limit

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print(
                "No pages" + (f" of type {page_type}." if page_type else "."), markup=False
            )
            return
        if not page_type:
            console.print(_type_counts(rows, truncated=truncated), highlight=False, markup=False)
        ordered = sorted(
            rows, key=lambda p: (str(p.get("page_type") or ""), str(p.get("title") or ""))
        )
        print_table(
            ["Type", "Title", "Page ID", "Updated"],
            [
                [
                    str(p.get("page_type") or ""),
                    short(p.get("title"), 50),
                    str(p.get("id") or ""),
                    str(p.get("updated_at") or "")[:10],
                ]
                for p in ordered
            ],
            title=f"Entities ({len(rows)})" + (" (more: --limit)" if len(rows) >= limit else ""),
        )

    def lines(rows: list[dict[str, Any]]) -> list[str]:
        # Piped (an agent), the counts lead too: they were only on a terminal.
        counts = [_type_counts(rows, truncated=truncated)] if rows and not page_type else []
        return [*counts, *compact_lines(rows)]

    emit(pages, json_out=json_out, render=render, lines=lines)


def _type_counts(rows: list[dict[str, Any]], *, truncated: bool) -> str:
    """``Types: organization 6  person 4 ...``, most pages first."""
    counts = Counter(str(p.get("page_type") or "untyped") for p in rows)
    line = "Types: " + "  ".join(f"{kind} {count}" for kind, count in counts.most_common())
    return line + (f"  (of the first {len(rows)} pages; more: --limit)" if truncated else "")


def conflicts(
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        "-s",
        help="Only this project.",
        callback=project_ref_callback,
    ),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=200, help="Most conflicts to list."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Card sections a later source contradicts, with the sources on each side."""
    scope = resolve_project(project) if project else None
    with get_sdk() as sdk:
        result = sdk.knowledge.conflicts(scope=scope, limit=limit)
    rows: list[dict[str, Any]] = result.get("data") or []

    def render(items: list[dict[str, Any]]) -> None:
        if not items:
            err_console.print("No open conflicts.")
            return
        print_table(
            ["Card", "Section", "Contradicting", "Review"],
            [
                [
                    short(c.get("page_title"), 40),
                    str(c.get("section_id") or ""),
                    short("; ".join(s.get("name") or "" for s in c.get("contradicting") or []), 40),
                    str(c.get("review_proposal_id") or "-"),
                ]
                for c in items
            ],
            title=f"Conflicts ({len(items)})",
        )

    emit(rows, json_out=json_out, render=render)


def undo_run(
    run: str = typer.Argument(..., help="An Ask or workflow run id, or an agent's run label."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be undone."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Take back every Knowledge write one run made (pending proposals dismissed,
    accepted ones reverted; changes later work built on are reported, not undone)."""
    if not dry_run:
        confirm_or_require_yes(f"Undo every Knowledge write of run {run}?", yes)
    with get_sdk() as sdk:
        result = sdk.knowledge.undo_run(run, dry_run=dry_run)

    def render(r: dict[str, Any]) -> None:
        verb = "Would undo" if r.get("dry_run") else "Undid"
        console.print(
            f"{verb} run {r.get('run')}: {len(r.get('dismissed') or [])} pending proposal(s) "
            f"dismissed, {len(r.get('reverted') or [])} change(s) reverted.",
            highlight=False,
            markup=False,
        )
        for conflict in r.get("conflicts") or []:
            err_console.print(
                f"  Not reverted (changed since): proposal {conflict.get('proposal_id')}",
                highlight=False,
                markup=False,
            )
        if r.get("truncated"):
            err_console.print("More remain: run the command again to continue.")

    emit(result, json_out=json_out, render=render)


def register(root: typer.Typer) -> None:
    """Attach ``kb entities``, ``history``, ``conflicts`` and ``undo-run`` (the root is unused)."""
    kb_app.command("entities")(entities)
    kb_app.command("history")(log)
    kb_app.command("conflicts")(conflicts)
    kb_app.command("undo-run")(undo_run)
