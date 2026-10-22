"""Project-map card commands on ``beakr kb``: rows and the row-level writes.

These live beside ``kb.py`` rather than in it (that module is already past a
thousand lines) and attach to the same ``kb`` Typer app. Each one is a thin
shell over the knowledge door, so argument names match the in-product tools.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import typer
from beakr_sdk.resources.knowledge import result_text
from rich.markup import escape

from beakr_cli.commands.kb import app as kb_app
from beakr_cli.output import console, emit, err_console
from beakr_cli.project_binding import resolve_project
from beakr_cli.proposal_view import staged_proposal_lines
from beakr_cli.sdk import get_sdk

_FINDING_KINDS = ("result", "lesson", "decision", "question", "correction")
_ROW_TABLES = ("facts", "connections", "findings")
_MATCH_MODES = ("exact", "similar", "meaning")
_ROW_VIEWS = ("values", "fields", "groups", "conflicts")
_EDIT_TABLES = ("facts", "connections", "findings")

_scope_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    "-s",
    help="Project id, slug or name. Defaults to the bound or default project.",
)
_rationale_opt = typer.Option(..., "--rationale", help="Why this change is right.")
_json_opt = typer.Option(False, "--json", "-j", help="Output the raw door result.")
_evidence_opt = typer.Option(
    None,
    "--evidence",
    help=(
        "The source: {{key}} token(s) or the reference a read prints (memo.pdf › p. 7). "
        "Optional with --cite-conversation."
    ),
)
_conversation_opt = typer.Option(
    None,
    "--cite-conversation",
    help=(
        "Cite this conversation as the source, under a short label of your choosing "
        "(cited as {{conversation:LABEL}}); needs --excerpt."
    ),
)
_excerpt_opt = typer.Option(
    None, "--excerpt", help="With --cite-conversation: the words in it that state this."
)
_source_title_opt = typer.Option(
    None,
    "--source-title",
    help="With --cite-conversation: what the conversation was (default: its label).",
)

#: The label an inline conversation citation may key itself by (the engine's
#: ``outside_citations._OWN_KEY``, past its ``conversation:``).
_CONVERSATION_LABEL = re.compile(r"[A-Za-z0-9_.:/-]{1,120}")


def _scope(explicit: str | None) -> str | None:
    # A slug or name resolves to the project id here, as every other project
    # option does; passed through raw, the door could not find a slug.
    return resolve_project(explicit)


def _print_result(result: dict[str, Any], *, json_out: bool) -> None:
    # Piped, an agent reads the door's text too (the rows table, the staged
    # proposal), not a summary of the reply's fields. A staged write's content
    # hash follows it: `kb proposals accept --reviewed-hash` takes it back.
    def text_lines(r: dict[str, Any]) -> list[str]:
        return [*result_text(r).splitlines(), *staged_proposal_lines(r)]

    emit(
        result,
        json_out=json_out,
        render=lambda r: console.print("\n".join(text_lines(r)), markup=False),
        lines=text_lines,
    )


def _cited(
    evidence: str | None,
    conversation: str | None,
    excerpt: str | None,
    source_title: str | None,
) -> tuple[str, list[dict[str, Any]] | None]:
    """The row's ``evidence`` and, citing a conversation, its inline ``citations``.

    A conversation is no stored source, so the door cites it as the MCP tools
    do: ``{{conversation:<label>}}`` in the evidence and a declared citation
    of source_type ``conversation`` with ``source_title`` and the words cited
    (``meta.excerpt``). Without it the token named nothing citeable.
    """
    if conversation is None:
        if excerpt is not None or source_title is not None:
            _usage("--excerpt and --source-title go with --cite-conversation.")
        if not (evidence or "").strip():
            _usage("Pass --evidence, or --cite-conversation with --excerpt.")
        return str(evidence), None
    label = conversation.strip().strip("{}").strip()
    label = label.removeprefix("conversation:")
    if not _CONVERSATION_LABEL.fullmatch(label):
        _usage(
            "--cite-conversation takes a short label: letters, digits and _ . : / - (up to 120)."
        )
    if not (excerpt or "").strip():
        _usage("--cite-conversation needs --excerpt: the words in the conversation cited.")
    key = f"conversation:{label}"
    citation = {
        "key": key,
        "source_type": "conversation",
        "source_title": (source_title or "").strip() or label,
        "meta": {"excerpt": str(excerpt).strip()},
    }
    token = "{{" + key + "}}"
    given = (evidence or "").strip()
    cited = f"{given} {token}" if given else token
    return cited, [citation]


def _usage(message: str) -> None:
    err_console.print(message, markup=False)
    raise typer.Exit(2)


def _choice(value: str, allowed: tuple[str, ...], option: str) -> str:
    if value not in allowed:
        err_console.print(f"{option} must be one of: {', '.join(allowed)}.", markup=False)
        raise typer.Exit(2)
    return value


def _parse_json(value: str, option: str) -> Any:
    """Inline JSON, or @path to a JSON file."""
    try:
        if value.startswith("@"):
            return json.loads(Path(value[1:]).read_text())
        return json.loads(value)
    except (OSError, ValueError) as exc:
        err_console.print(f"{option}: not valid JSON ({exc}).", markup=False)
        raise typer.Exit(2) from exc


def _parse_row(value: str, option: str) -> Any:
    """A row is a JSON object, or plain row text as the card shows it."""
    stripped = value.strip()
    if stripped.startswith("{"):
        return _parse_json(stripped, option)
    return value


def rows(
    table: str = typer.Option("facts", "--table", "-t", help="facts, connections or findings."),
    entity: str | None = typer.Option(
        None,
        "--entity",
        "-e",
        help="Only these cards: one title, or several as a|b|c or ^(a|b)$.",
    ),
    attribute: str | None = typer.Option(
        None, "--attribute", "-a", help="The field: by name, alias or meaning (see --match)."
    ),
    match: str | None = typer.Option(
        None,
        "--match",
        help=(
            "How --attribute finds a field: exact (the name or an alias), similar (also "
            "names holding its words) or meaning (also the closest by meaning, where the "
            "rows index is on). Default: exact when a field has that name, else meaning. "
            "Replies say which field was used."
        ),
    ),
    view: str | None = typer.Option(
        None,
        "--view",
        help=(
            "values (default: the rows), fields (the field names and their counts), groups, "
            "or conflicts (one card stating different values for a field at the same When)."
        ),
    ),
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
        None, "--to", help="Connections: the rows pointing at this page (its incoming links)."
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
    after_card: str | None = typer.Option(
        None, "--after-card", help="Continue a capped card scan after this card (as a reply names)."
    ),
    evidence: bool = typer.Option(False, "--evidence", help="Include each row's cited passage."),
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Every map card's rows as one table (facts, connections or findings).

    Each row shows its entity (the card's title), entity_type (page type), the
    field (attribute or relation), the value (or to), When, its sources and a
    citation; the header names the table. Filter by --type, --entity and --table.
    """
    _choice(table, _ROW_TABLES, "--table")
    if match is not None:
        _choice(match, _MATCH_MODES, "--match")
    if view is not None:
        _choice(view, _ROW_VIEWS, "--view")
    # The door's default match also takes names holding the attribute's words,
    # so --attribute Enrollment answered "Enrollment target rationale" rows
    # beside Enrollment's own (swarm 018). A field with exactly that name wins:
    # the exact name is asked first, and the default match only when that
    # answers no rows. --match similar or meaning keeps the wider reading.
    exact_first = bool(attribute) and match is None and view in (None, "values", "conflicts")
    arguments = {
        "table": table,
        "entity": entity,
        "attribute": attribute,
        "view": view,
        "query": query,
        "start_date": start,
        "end_date": end,
        "current_only": current_only or None,
        "as_of": as_of,
        "to": to,
        "connected_to": connected_to,
        "group_by": group_by,
        "order_by": order_by,
        "descending": descending or None,
        "page_type": page_type,
        "limit": limit,
        "offset": offset,
        "after_card": after_card,
        "include_evidence": evidence or None,
        "scope": _scope(scope),
    }
    with get_sdk() as sdk:
        # The generic door call: the SDK's rows() predates to, connected_to, as_of
        # and group_by, and the door takes every rows argument by its tool name.
        result = sdk.knowledge.command("rows", match="exact" if exact_first else match, **arguments)
        if exact_first and _no_rows(result):
            err_console.print(
                f"No field is named exactly {attribute!r}; these rows are the default "
                "match (names holding its words, or the closest by meaning). "
                "Pass --match exact to keep only that name.",
                markup=False,
            )
            result = sdk.knowledge.command("rows", **arguments)
    _print_result(result, json_out=json_out)


def _no_rows(result: Any) -> bool:
    """Whether a rows reply found nothing (the door's ``empty`` outcome)."""
    if not isinstance(result, dict):
        return False
    outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else {}
    coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
    return outcome.get("status") == "empty" or coverage.get("total") == 0


def query(
    text: str | None = typer.Argument(
        None,
        help=(
            'One SQL SELECT, e.g. "SELECT value, COUNT(DISTINCT entity) AS cards FROM facts '
            "WHERE entity_type = 'matter' AND field = 'Current status' GROUP BY value\", or the "
            "same query as a JSON spec (text starting with {). Use - to read it from stdin."
        ),
    ),
    query_file: Path | None = typer.Option(
        None, "--file", "-f", help="Read the query (SQL or a JSON spec) from this file instead."
    ),
    cursor: str | None = typer.Option(
        None, "--cursor", help="Continue a previous answer with the cursor it gave."
    ),
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Joins, groups and aggregates over every map card's rows, where enabled.

    For what `kb rows` cannot express in one call. Write one SELECT: FROM facts,
    connections or findings; up to two JOINs on SAME_CARD(a) or TARGET_CARD(a);
    WHERE with AND/OR/NOT on entity, entity_type, field (= exact name, LIKE
    words, ~ meaning), value, when, to, connected_to; AS OF a day or
    CURRENT_DATE; GROUP BY with COUNT, MIN, MAX, SUM, AVG; ORDER BY; LIMIT (50,
    at most 200). Beakr parses it into a structured query and never runs it as
    SQL. A JSON spec (from, where, join, group_by, aggregate, order_by, limit,
    semantic) is accepted too. The reply opens with what the query was
    understood as, then the total, one window and a cursor when more rows follow.
    """
    raw = _query_text(text, query_file).strip()
    arguments: dict = {}
    if raw.startswith("{"):
        try:
            arguments["spec"] = json.loads(raw)
        except ValueError as exc:
            err_console.print(
                f"[red]The JSON spec is not valid JSON:[/red] {escape(str(exc.msg))}."
            )
            raise typer.Exit(1) from exc
    else:
        arguments["sql"] = raw
    if cursor:
        arguments["cursor"] = cursor
    with get_sdk() as sdk:
        result = sdk.knowledge.command("query", scope=_scope(scope), **arguments)
    _print_result(result, json_out=json_out)


def _query_text(text: str | None, query_file: Path | None) -> str:
    import sys

    if query_file is not None:
        return query_file.read_text(encoding="utf-8")
    if text == "-":
        return sys.stdin.read()
    if text and text.strip():
        return text
    err_console.print("[red]Give the query as SQL or JSON, '-' for stdin, or --file PATH.[/red]")
    raise typer.Exit(1)


def add_fact(
    page: str = typer.Argument(..., help="Card title, slug or ID."),
    attribute: str = typer.Option(..., "--attribute", help="Stable attribute name."),
    value: str = typer.Option(..., "--value", help="The statement as the source gives it."),
    evidence: str | None = _evidence_opt,
    when: str | None = typer.Option(None, "--when", help="YYYY-MM-DD, YYYY-MM, YYYY-Qn or YYYY."),
    cite_conversation: str | None = _conversation_opt,
    excerpt: str | None = _excerpt_opt,
    source_title: str | None = _source_title_opt,
    rationale: str = _rationale_opt,
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Propose one Facts row on a project-map card."""
    cited, citations = _cited(evidence, cite_conversation, excerpt, source_title)
    with get_sdk() as sdk:
        result = sdk.knowledge.add_fact(
            page,
            attribute=attribute,
            value=value,
            evidence=cited,
            citations=citations,
            when=when,
            rationale=rationale,
            scope=_scope(scope),
        )
    _print_result(result, json_out=json_out)


def add_connection(
    page: str = typer.Argument(..., help="Card title, slug or ID."),
    relation: str = typer.Option(..., "--relation", help="Short verb phrase."),
    to: str = typer.Option(..., "--to", help="The other card's title."),
    evidence: str | None = _evidence_opt,
    when: str | None = typer.Option(None, "--when", help="When the connection held."),
    cite_conversation: str | None = _conversation_opt,
    excerpt: str | None = _excerpt_opt,
    source_title: str | None = _source_title_opt,
    rationale: str = _rationale_opt,
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Propose one Connections row on a project-map card."""
    cited, citations = _cited(evidence, cite_conversation, excerpt, source_title)
    with get_sdk() as sdk:
        result = sdk.knowledge.add_connection(
            page,
            relation=relation,
            to=to,
            evidence=cited,
            citations=citations,
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
    evidence: str | None = _evidence_opt,
    about: list[str] | None = typer.Option(None, "--about", help="What it is about (repeat)."),
    finding_scope: str | None = typer.Option(None, "--finding-scope", help="Where it holds."),
    supersedes: str | None = typer.Option(None, "--supersedes", help="Finding it replaces."),
    cite_conversation: str | None = _conversation_opt,
    excerpt: str | None = _excerpt_opt,
    source_title: str | None = _source_title_opt,
    rationale: str = _rationale_opt,
    scope: str | None = _scope_opt,
    json_out: bool = _json_opt,
) -> None:
    """Propose one dated Findings bullet on a project-map card."""
    _choice(kind, _FINDING_KINDS, "--kind")
    cited, citations = _cited(evidence, cite_conversation, excerpt, source_title)
    about_value: str | list[str] | None = None
    if about:
        about_value = about[0] if len(about) == 1 else list(about)
    with get_sdk() as sdk:
        # The SDK's add_finding takes no citations; its write() is the same call.
        result = sdk.knowledge.write(
            "add_finding",
            page=page,
            date=date,
            kind=kind,
            text=text,
            evidence=cited,
            citations=citations,
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
    kb_app.command("query")(query)
    kb_app.command("add-fact")(add_fact)
    kb_app.command("add-connection")(add_connection)
    kb_app.command("add-finding")(add_finding)
    kb_app.command("edit-rows")(edit_rows)
