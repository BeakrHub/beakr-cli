"""Project-map card commands on ``beakr kb``: rows and the row-level writes.

These live beside ``kb.py`` rather than in it (that module is already past a
thousand lines) and attach to the same ``kb`` Typer app. Each one is a thin
shell over the knowledge door, so argument names match the in-product tools.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from beakr_sdk.resources.knowledge import result_text

from beakr_cli.commands.kb import app as kb_app
from beakr_cli.output import console, emit, err_console
from beakr_cli.project_binding import resolve_project
from beakr_cli.sdk import get_sdk

_FINDING_KINDS = ("result", "lesson", "decision", "question", "correction")
_ROW_TABLES = ("facts", "connections", "findings")
_EDIT_TABLES = ("facts", "connections", "findings")

_scope_opt = typer.Option(
    None,
    "--scope",
    "-s",
    help="Project name or ID. Defaults to the bound or default project.",
)
_rationale_opt = typer.Option(..., "--rationale", help="Why this change is right.")
_json_opt = typer.Option(False, "--json", "-j", help="Output the raw door result.")


def _scope(explicit: str | None) -> str | None:
    return explicit or resolve_project(None)


def _print_result(result: dict[str, Any], *, json_out: bool) -> None:
    emit(result, json_out=json_out, render=lambda r: console.print(result_text(r), markup=False))


def _choice(value: str, allowed: tuple[str, ...], option: str) -> str:
    if value not in allowed:
        err_console.print(f"{option} must be one of: {', '.join(allowed)}.")
        raise typer.Exit(2)
    return value


def _parse_json(value: str, option: str) -> Any:
    """Inline JSON, or @path to a JSON file."""
    try:
        if value.startswith("@"):
            return json.loads(Path(value[1:]).read_text())
        return json.loads(value)
    except (OSError, ValueError) as exc:
        err_console.print(f"{option}: not valid JSON ({exc}).")
        raise typer.Exit(2) from exc


def _parse_row(value: str, option: str) -> Any:
    """A row is a JSON object, or plain row text as the card shows it."""
    stripped = value.strip()
    if stripped.startswith("{"):
        return _parse_json(stripped, option)
    return value


def rows(
    table: str = typer.Option("facts", "--table", "-t", help="facts, connections or findings."),
    entity: str | None = typer.Option(None, "--entity", "-e", help="Only this card."),
    attribute: str | None = typer.Option(None, "--attribute", "-a", help="Only this attribute."),
    query: str | None = typer.Option(None, "--query", "-q", help="Text filter."),
    start: str | None = typer.Option(None, "--start", help="Start date YYYY-MM-DD."),
    end: str | None = typer.Option(None, "--end", help="End date YYYY-MM-DD."),
    current_only: bool = typer.Option(
        False, "--current-only", help="Keep the latest When per attribute."
    ),
    as_of: str | None = typer.Option(
        None, "--as-of", help="Facts: each card's value on this date (YYYY-MM-DD)."
    ),
    to: str | None = typer.Option(
        None, "--to", help="Connections: every row involving this page."
    ),
    connected_to: str | None = typer.Option(
        None, "--connected-to", help="Only cards linked to this page, either direction."
    ),
    group_by: str | None = typer.Option(
        None, "--group-by", help="Counts and totals per entity, entity_type, year or a column."
    ),
    order_by: str | None = typer.Option(
        None, "--order-by", help="Sort column; facts also amount or date."
    ),
    descending: bool = typer.Option(False, "--desc", help="Sort descending."),
    page_type: str | None = typer.Option(None, "--type", help="Card page type."),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max rows."),
    offset: int | None = typer.Option(None, "--offset", help="Skip this many rows."),
    evidence: bool = typer.Option(False, "--evidence", help="Include each row's cited passage."),
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Every map card's rows as one table (facts, connections or findings)."""
    _choice(table, _ROW_TABLES, "--table")
    with get_sdk() as sdk:
        # The generic door call: the SDK's rows() predates to, connected_to, as_of
        # and group_by, and the door takes every rows argument by its tool name.
        result = sdk.knowledge.command(
            "rows",
            table=table,
            entity=entity,
            attribute=attribute,
            query=query,
            start_date=start,
            end_date=end,
            current_only=current_only or None,
            as_of=as_of,
            to=to,
            connected_to=connected_to,
            group_by=group_by,
            order_by=order_by,
            descending=descending or None,
            page_type=page_type,
            limit=limit,
            offset=offset,
            include_evidence=evidence or None,
            scope=_scope(scope),
        )
    _print_result(result, json_out=json_out)


def add_fact(
    page: str = typer.Argument(..., help="Card title, slug or ID."),
    attribute: str = typer.Option(..., "--attribute", help="Stable attribute name."),
    value: str = typer.Option(..., "--value", help="The statement as the source gives it."),
    evidence: str = typer.Option(..., "--evidence", help="{{key}} citation token(s)."),
    when: str | None = typer.Option(None, "--when", help="YYYY-MM-DD, YYYY-MM, YYYY-Qn or YYYY."),
    rationale: str = _rationale_opt,
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Propose one Facts row on a project-map card."""
    with get_sdk() as sdk:
        result = sdk.knowledge.add_fact(
            page,
            attribute=attribute,
            value=value,
            evidence=evidence,
            when=when,
            rationale=rationale,
            scope=_scope(scope),
        )
    _print_result(result, json_out=json_out)


def add_connection(
    page: str = typer.Argument(..., help="Card title, slug or ID."),
    relation: str = typer.Option(..., "--relation", help="Short verb phrase."),
    to: str = typer.Option(..., "--to", help="The other card's title."),
    evidence: str = typer.Option(..., "--evidence", help="{{key}} citation token(s)."),
    when: str | None = typer.Option(None, "--when", help="When the connection held."),
    rationale: str = _rationale_opt,
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Propose one Connections row on a project-map card."""
    with get_sdk() as sdk:
        result = sdk.knowledge.add_connection(
            page,
            relation=relation,
            to=to,
            evidence=evidence,
            when=when,
            rationale=rationale,
            scope=_scope(scope),
        )
    _print_result(result, json_out=json_out)


def add_finding(
    page: str = typer.Argument(..., help="Card title, slug or ID."),
    date: str = typer.Option(..., "--date", help="YYYY-MM-DD the finding was reached."),
    kind: str = typer.Option(..., "--kind", help="result|lesson|decision|question|correction."),
    text: str = typer.Option(..., "--text", help="The finding."),
    evidence: str = typer.Option(..., "--evidence", help="{{key}} citation token(s)."),
    about: list[str] | None = typer.Option(None, "--about", help="What it is about (repeat)."),
    finding_scope: str | None = typer.Option(None, "--finding-scope", help="Where it holds."),
    supersedes: str | None = typer.Option(None, "--supersedes", help="Finding it replaces."),
    rationale: str = _rationale_opt,
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Propose one dated Findings bullet on a project-map card."""
    _choice(kind, _FINDING_KINDS, "--kind")
    about_value: str | list[str] | None = None
    if about:
        about_value = about[0] if len(about) == 1 else list(about)
    with get_sdk() as sdk:
        result = sdk.knowledge.add_finding(
            page,
            date=date,
            kind=kind,
            text=text,
            evidence=evidence,
            about=about_value,
            finding_scope=finding_scope,
            supersedes=supersedes,
            rationale=rationale,
            scope=_scope(scope),
        )
    _print_result(result, json_out=json_out)


def edit_rows(
    page: str = typer.Argument(..., help="Card title, slug or ID."),
    table: str = typer.Option(..., "--table", "-t", help="facts, connections or findings."),
    old_row: list[str] | None = typer.Option(
        None,
        "--old-row",
        help="Row to replace, JSON or row text (repeat). A finding is {date, text}.",
    ),
    row: list[str] | None = typer.Option(
        None, "--row", help="Replacement row JSON (repeat; none deletes the old rows)."
    ),
    reason: str | None = typer.Option(None, "--reason", help="Required to delete rows."),
    edits: str | None = typer.Option(
        None, "--edits", help="JSON array of {old_rows, rows, reason}, or @path."
    ),
    rationale: str = _rationale_opt,
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Edit, merge or delete rows already on a card."""
    _choice(table, _EDIT_TABLES, "--table")
    if bool(edits) == bool(old_row):
        err_console.print("Provide either --old-row (with --row) or --edits.")
        raise typer.Exit(2)
    with get_sdk() as sdk:
        if edits:
            parsed = _parse_json(edits, "--edits")
            if not isinstance(parsed, list):
                err_console.print("--edits must be a JSON array.")
                raise typer.Exit(2)
            result = sdk.knowledge.edit_rows(
                page, table=table, edits=parsed, rationale=rationale, scope=_scope(scope)
            )
        else:
            result = sdk.knowledge.edit_rows(
                page,
                table=table,
                old_rows=[_parse_row(value, "--old-row") for value in old_row or []],
                # An empty list is meaningful: it deletes the old rows.
                rows=[_parse_json(value, "--row") for value in row or []],
                reason=reason,
                rationale=rationale,
                scope=_scope(scope),
            )
    _print_result(result, json_out=json_out)


def register(root: typer.Typer) -> None:
    """Attach the card commands to ``beakr kb`` (the root is unused)."""
    kb_app.command("rows")(rows)
    kb_app.command("add-fact")(add_fact)
    kb_app.command("add-connection")(add_connection)
    kb_app.command("add-finding")(add_finding)
    kb_app.command("edit-rows")(edit_rows)
