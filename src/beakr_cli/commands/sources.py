"""``beakr sources ls|attach|edit|rm|sync|recompile|status|tracked|...``: what a project reads.

A project's sources are the connected items it reads: a Google Drive folder, a
Slack channel, a Notion page or database, or everything one connection can see.
``attach`` takes the item's link (``--url``, or the link as the argument) or its
provider, id, type and name; ``--knowledge`` turns Knowledge on for it, and
``sync`` (or ``attach --sync``) starts pulling it. Connecting the account itself
is ``beakr integrations connect``.

A source's integration rules narrow what a sync takes: ``--query`` (a Notion
database filter or saved view, or a Drive query, provider-native JSON),
``--mode agent|all`` (Beakr's source picker chooses, or every item in scope), and
plain-language ``--guidance`` that the picker and the Knowledge compiler read.
They are sent only when given, so the commands work with engines that
predate them.

With an agent's credential, attaching, changing and syncing a source are refused
(exit 6) while the project requires a person to approve what agents bring into
Knowledge; the person runs the command themselves.

``tracked`` lists every item a source's syncs picked and what became of it
(ingested, pending, skipped or removed), with why a skipped item was set aside,
including when Knowledge read it and found nothing to add, and why a failed
item's compile did not finish. ``status`` says the same for the items the
latest sync brought in, and ``import-status`` how a source's history import
went.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

import click
import typer
from beakr_sdk.errors import (
    BeakrError,
    ConflictError,
    NotFoundError,
    NotProvidedError,
    PermissionDeniedError,
)

from beakr_cli import cancels, connected_sources
from beakr_cli.compact import cell
from beakr_cli.errors import (
    EXIT_AUTH,
    EXIT_NOT_FOUND,
    describe_error,
    error_object,
    exit_code_for,
)
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk
from beakr_cli.source_links import (
    FOLDER_SOURCE_NAMES,
    SourceLinkError,
    parse_source_link,
    provider_slug,
)

app = typer.Typer(
    help=(
        "Attach Drive folders, Slack channels, Notion pages and other connected items to "
        "a project, turn Knowledge on for them, and sync them."
    ),
    no_args_is_help=True,
)

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    help="Project id, slug or name (default: the bound or configured project).",
    callback=project_ref_callback,
)
_source_arg = typer.Argument(..., help="Source id from `beakr sources ls`.")

#: ``--mode``: how a source's items are chosen for Knowledge.
PULL_MODES = ("agent", "all")
#: Providers whose sync ignores ``pull_mode``: on Notion the rule decides, and
#: attaching brings in everything under the item that passes it (Max, 2026-11-21).
#: The engine still stores the value, so the note says so (e2e-live H03).
MODE_IGNORED = {"notion:nango": "Notion"}
_MODE_HELP = "agent (Beakr's source picker chooses; default) or all (every item). Not for Notion."
#: ``--query``'s shape, as the engine takes ``source_query``: exactly one key, the
#: provider's own (`beakr sources keys` lists them).
_QUERY_SHAPES = (
    """one key, e.g. '{"jql": "project = LAB"}', '{"notion_view": "<view id>"}' or """
    """'{"slack_filter": {...}}'; `beakr sources keys` lists every key"""
)
#: Shorthands for the text keys people type most: ``--jql 'project = LAB'`` is
#: ``--query '{"jql": "project = LAB"}'``.
QUERY_SHORTHANDS = {
    "--drive-q": "drive_q",
    "--gmail-q": "gmail_q",
    "--jql": "jql",
    "--cql": "cql",
    "--github-q": "github_q",
}
#: ``--scope-mode``: one item, or everything the connection can read.
SCOPE_MODES = ("item", "all")


class NoConnection(click.ClickException):
    """No connection for the provider: connect one first (exit 4, not found)."""

    exit_code = EXIT_NOT_FOUND


class ConnectionsRefused(click.ClickException):
    """The credential may not list the connections to choose from (exit 3)."""

    exit_code = EXIT_AUTH


def _note_mode(mode: str | None, provider: str | None) -> None:
    """Say, on stderr, that --mode changes nothing for a provider whose rule decides."""
    name = MODE_IGNORED.get(provider or "")
    if mode and name:
        err_console.print(
            f"Note: --mode is stored but not used for {name} item sources; the rule decides.",
            markup=False,
            highlight=False,
        )


def _require_project(project: str | None) -> str:
    project_id = resolve_project(project)
    if not project_id:
        raise click.UsageError("Pass --project or run `beakr init` to bind this directory to one.")
    return str(project_id)


def source_command(verb: str, source_id: Any, project_id: str, *flags: str) -> str:
    """``beakr sources VERB SOURCE_ID [FLAGS] -P PROJECT``, quoted to paste as it is.

    Every per-source command needs the project (``_require_project``), and an
    agent runs a ``next:`` line as printed, often outside a directory bound to
    the project, so a suggested command always names it (r108 sweep, 26 Nov
    2026: ``next: beakr sources sync <id>`` failed with "Pass --project").
    """
    return shlex.join(["beakr", "sources", verb, str(source_id), *flags, "-P", str(project_id)])


def _choice(value: str | None, allowed: tuple[str, ...], flag: str) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise typer.BadParameter(f"use one of {', '.join(allowed)}", param_hint=flag)
    return value


_query_opt = typer.Option(
    None, "--query", help=f"Only matching items, provider-native JSON: {_QUERY_SHAPES}."
)
_drive_q_opt = typer.Option(None, "--drive-q", help="Shorthand for --query '{\"drive_q\": ...}'.")
_gmail_q_opt = typer.Option(None, "--gmail-q", help="Shorthand for --query '{\"gmail_q\": ...}'.")
_jql_opt = typer.Option(None, "--jql", help="Shorthand for --query '{\"jql\": ...}' (Jira).")
_cql_opt = typer.Option(None, "--cql", help="Shorthand for --query '{\"cql\": ...}' (Confluence).")
_github_q_opt = typer.Option(
    None, "--github-q", help="Shorthand for --query '{\"github_q\": ...}'."
)


def _query(raw: str | None, **shorthands: str | None) -> dict[str, Any] | None:
    """``--query`` (a JSON object) or one shorthand, as the source's ``source_query``.

    ``shorthands`` maps each shorthand's key (``jql``) to its value; at most one
    of them and ``--query`` may be given.
    """
    given = [f"--{key.replace('_', '-')}" for key, value in shorthands.items() if value is not None]
    if raw is not None:
        given.insert(0, "--query")
    if len(given) > 1:
        raise click.UsageError(f"Give one query: {', '.join(given)} each set one.")
    for key, value in shorthands.items():
        if value is not None:
            return {key: value}
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"not JSON: {exc}", param_hint="--query") from exc
    if not isinstance(parsed, dict) or len(parsed) != 1:
        raise typer.BadParameter(f"give a JSON object with {_QUERY_SHAPES}", param_hint="--query")
    return parsed


def _query_keys(sdk: Any, *, refused: bool = True) -> list[dict[str, Any]] | None:
    """The engine's ``source_query`` keys, or None when they cannot be listed.

    None from an engine that does not list them, and (with ``refused``) for a
    credential that may not: a project-limited key, since the listing names no
    project. The query is then sent as given and the engine judges it.
    """
    try:
        listing = sdk.project_sources.query_keys()
    except NotFoundError:
        return None
    except PermissionDeniedError:
        if refused:
            return None
        raise
    return list((listing or {}).get("keys") or [])


def _check_query(
    keys: list[dict[str, Any]] | None,
    source_query: dict[str, Any] | None,
    *,
    provider: str | None = None,
    item_type: str | None = None,
    type_unsure: bool = False,
) -> None:
    """Refuse a query key the engine does not list, or one the source's provider does not take.

    The engine decides (``GET /v1/integrations/source-query-keys``); this only
    answers before a request is sent, naming the keys that would fit. Without the
    listing (an older engine) the query is sent as given and the engine judges it.
    ``type_unsure``: ``item_type`` is a guess from a link (Notion could not be
    asked), so a refusal says how to give the type.
    """
    if not source_query or keys is None:
        return
    key = next(iter(source_query))
    by_key = {str(spec.get("key")): spec for spec in keys}
    fitting = sorted(
        name
        for name, spec in by_key.items()
        if provider is None or provider in (spec.get("providers") or [])
    )
    where = f" for {provider}" if provider else ""
    spec = by_key.get(key)
    if spec is None or (provider is not None and key not in fitting):
        raise click.UsageError(
            f"{key!r} is not a source query key{where}. "
            + (f"Use one of: {', '.join(fitting)}." if fitting else "None applies here.")
            + " `beakr sources keys` lists each with an example."
        )
    if item_type is None:
        return
    if _takes(spec, item_type):
        return
    allowed = spec.get("item_types")
    takes = f"only a {' or '.join(allowed)}" if allowed is not None else "no " + item_type
    instead = [name for name in fitting if _takes(by_key[name], item_type)]
    if type_unsure and allowed:
        raise click.UsageError(
            f"{key!r} applies to {takes}, and Beakr could not ask Notion what this item is "
            f"(its link reads as a {item_type}). If it is a {allowed[0]}, pass --type "
            f"{allowed[0]}."
        )
    raise click.UsageError(
        f"{key!r} applies to {takes}; this item is a {item_type}."
        + (f" For a {item_type} use one of: {', '.join(instead)}." if instead else "")
    )


def _takes(spec: dict[str, Any], item_type: str) -> bool:
    """Whether a source query key applies to an attachment of ``item_type``."""
    allowed, excluded = spec.get("item_types"), spec.get("excluded_item_types") or []
    return (allowed is None or item_type in allowed) and item_type not in excluded


def _group_count(group: dict[str, Any]) -> str:
    """A breakdown entry's count, or that a rule left it out."""
    if group.get("excluded_reason"):
        return f"left out ({group['excluded_reason']})"
    capped = "at least " if group.get("count_capped") else ""
    return f"{capped}{int(group.get('count') or 0)}"


def _preview_lines(data: dict[str, Any]) -> list[str]:
    count = int(data.get("count") or 0)
    head = [
        "rejected" if data.get("provider_status") == "rejected" else "ok",
        f"count={'>=' if data.get('count_capped') else ''}{count}",
    ]
    if data.get("message"):
        head.append(f"message={cell(data['message'], width=200)}")
    lines = ["\t".join(head)]
    lines += [
        "\t".join(
            [
                "sample",
                cell(item.get("id") or "-"),
                cell(item.get("name") or ""),
                # When it last changed or was sent (engine #3061); older engines send none.
                *([cell(str(item["date"]))] if item.get("date") else []),
            ]
        )
        for item in (data.get("sample") or [])[:10]
    ]
    lines += [
        "\t".join(
            [
                "group",
                cell(group.get("id") or "-"),
                cell(group.get("name") or ""),
                f"count={'>=' if group.get('count_capped') else ''}{int(group.get('count') or 0)}",
                *(
                    [f"excluded={cell(group['excluded_reason'], width=200)}"]
                    if group.get("excluded_reason")
                    else []
                ),
            ]
        )
        for group in data.get("breakdown") or []
    ]
    return lines


def _render_preview(data: dict[str, Any]) -> None:
    if data.get("provider_status") == "rejected":
        console.print(
            f"The provider rejected the query: {data.get('message') or 'no reason given'}. "
            "A rejected query cannot sync.",
            highlight=False,
            markup=False,
        )
        return
    count = int(data.get("count") or 0)
    noun = "item" if count == 1 else "items"
    amount = (
        f"At least {count} {noun} match"
        if data.get("count_capped")
        else (f"{count} {noun} {'matches' if count == 1 else 'match'}")
    )
    console.print(f"{amount}.", highlight=False, markup=False)
    if data.get("note"):
        err_console.print(str(data["note"]), markup=False)
    breakdown = data.get("breakdown") or []
    if breakdown:
        console.print("By database:", markup=False)
    for group in breakdown:
        console.print(
            f"  {group.get('name') or group.get('id')}: {_group_count(group)}",
            highlight=False,
            markup=False,
            soft_wrap=True,
        )
    sample = (data.get("sample") or [])[:10]
    if sample:
        console.print("For example:", markup=False)
    for item in sample:
        title = item.get("name") or item.get("id")
        date = f"  {item['date']}" if item.get("date") else ""
        url = f"  {item['url']}" if item.get("url") else ""
        console.print(f"  {title}{date}{url}", highlight=False, markup=False, soft_wrap=True)


def _emit_preview(data: Any, json_out: bool) -> None:
    """Print a rules preview; a query the provider rejected exits 1."""
    result = dict(data or {})
    emit(result, json_out=json_out, render=_render_preview, lines=_preview_lines)
    if result.get("provider_status") == "rejected":
        raise typer.Exit(1)


def _connection_label(row: dict[str, Any]) -> str:
    return f"{row.get('id')} ({row.get('label') or row.get('provider')})"


def _connections_refused(provider: str, exc: BeakrError) -> ConnectionsRefused:
    notion = ' and --type page|database --name "..."' if provider == "notion:nango" else ""
    return ConnectionsRefused(
        f"This credential cannot list the connections to choose one ({exc}). Pass the "
        f"connection with --connector <connection id>{notion}: the connection's id is "
        "under Settings > Integrations in Beakr, or in `beakr integrations ls` run with "
        "an unrestricted credential."
    )


def project_connections_path(project_id: str) -> str:
    return f"/v1/projects/{project_id}/integrations"


def _all_pages(first: Any, fetch: Any) -> list[dict[str, Any]]:
    """Every row of a cursor-paged list, from its first page and a fetch(cursor)."""
    rows: list[dict[str, Any]] = []
    page = first if isinstance(first, dict) else {}
    seen: set[str] = set()
    while True:
        rows.extend(row for row in page.get("data") or [] if isinstance(row, dict))
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor or cursor in seen:
            return rows
        seen.add(cursor)
        page = fetch(cursor)
        page = page if isinstance(page, dict) else {}


def connector_for(sdk: Any, project_id: str, provider: str) -> str:
    """The connection to attach ``provider``'s item with, when --connector names none (B11).

    The project's own list comes first (``GET /v1/projects/{id}/integrations``):
    the connections ``POST .../sources`` takes, which a key limited to the
    project may read. An engine without that route (404) falls back to the
    caller's organization-wide list (``GET /v1/integrations``), which such a key
    may not call; its refusal says to pass --connector.
    """
    path = project_connections_path(project_id)
    try:
        connections = _all_pages(
            sdk.request("GET", path),
            lambda cursor: sdk.request("GET", path, params={"cursor": cursor}),
        )
    except NotProvidedError:
        # An engine before the route. (A project that is missing answers a plain
        # NotFoundError with its own message, which is shown as it is.)
        try:
            listing = sdk.integrations.list()
        except PermissionDeniedError as refused:
            raise _connections_refused(provider, refused) from refused
        connections = (listing or {}).get("data") or []
    except PermissionDeniedError as exc:
        raise _connections_refused(provider, exc) from exc
    return pick_connector(connections, provider)


def pick_connector(connections: list[dict[str, Any]], provider: str) -> str:
    """The one connection for ``provider``; refuse none and several.

    The engine falls back to the caller's first usable connection when none is
    named, so with two (a personal and an organization Drive) it would pick one
    silently. The CLI names the connection, or asks for ``--connector``.
    """
    matching = [row for row in connections if row.get("provider") == provider]
    if not matching:
        raise NoConnection(
            f"You have no {provider} connection. Connect one with `beakr integrations connect` "
            "(see `beakr integrations ls`), or pass --connector."
        )
    if len(matching) > 1:
        raise click.UsageError(
            f"You have {len(matching)} {provider} connections: "
            + "; ".join(_connection_label(row) for row in matching)
            + ". Pass --connector with one of their ids."
        )
    return str(matching[0]["id"])


#: The engine's read-only integration tool that reads one Notion page or database:
#: its ``kind`` and title (``POST /v1/integrations/tools/notion_metadata/call``,
#: the call Beakr's own agent makes).
NOTION_LOOKUP_TOOL = "notion_metadata"
#: The kinds an attachment of a Notion item takes.
NOTION_KINDS = ("page", "database")


def notion_item(sdk: Any, connector_id: str, item_id: str) -> dict[str, str | None] | None:
    """``{"kind", "name"}`` of a Notion item as Notion reports it, or None if it cannot say.

    A Notion link alone does not tell a page from a database: only a link that
    opened one of a database's saved views (``?v=``) does, and a database's own
    link reads like a page's (e2e-live H, 2026-11-21: a bare database link was
    attached as a page and its ``notion_filter`` refused). The engine's
    ``notion_metadata`` tool looks the id up on the attaching connection. None when
    the lookup is not possible: an engine without the tool, a credential that may
    not read Notion, an item outside the connection's scope, or an id Notion does
    not know; the caller falls back to what the link says.
    """
    handle = f"beakr://external/{connector_id}/notion/{item_id}"
    try:
        result = sdk.integrations.call_tool(
            NOTION_LOOKUP_TOOL, {"handle": handle}, connector_id=connector_id
        )
    except BeakrError:
        return None
    if not isinstance(result, dict) or result.get("is_error"):
        return None
    content = result.get("content")
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("error") or data.get("kind") not in NOTION_KINDS:
        return None
    name = str(data.get("name") or "").strip()
    return {"kind": str(data["kind"]), "name": name or None}


def _same_item(a: Any, b: Any) -> bool:
    """Whether two external ids name one item (Notion's dashed and plain forms alike)."""
    return str(a or "").replace("-", "").lower() == str(b or "").replace("-", "").lower()


def _refuse_duplicate(
    sdk: Any, project_id: str, provider: str, item_id: str, exc: BeakrError
) -> None:
    """Turn an attach refused because the item is already attached into "Already attached".

    The engine answers a duplicate with 409 naming the existing source; before
    that change it answered 500 (a unique-index violation), whose "retry with
    backoff" was the wrong advice (e2e-live H10). The existing source is read
    from the 409's details, else from the project's listing. A 500 that is not
    a duplicate keeps its message but not the advice to retry. Raises, or
    returns for the caller to re-raise ``exc``.
    """
    details = exc.details or {}
    existing: dict[str, Any] | None = None
    found_id = details.get("source_id") or details.get("attachment_id")
    if found_id:
        existing = {
            "id": found_id,
            "external_item_name": details.get("external_item_name") or details.get("name"),
        }
    else:
        try:
            existing = next(
                (
                    row
                    for row in sdk.project_sources.list(project_id)
                    if row.get("provider") == provider
                    and _same_item(row.get("external_item_id"), item_id)
                ),
                None,
            )
        except BeakrError:
            existing = None
    if existing is None:
        if exc.status != 409:
            exc.fix = (
                "Nothing was attached as far as Beakr can tell; `beakr sources ls` shows the "
                "project's sources. If it keeps failing, report the request id."
            )
        return
    source_id = str(existing.get("id"))
    name = existing.get("external_item_name") or source_id
    raise ConflictError(
        f"Already attached as {name} ({source_id}).",
        code="already_attached",
        fix=(
            f"Change it with `{source_command('edit', source_id, project_id)}` (rules, "
            "Knowledge, nightly sync), or detach it first with "
            f"`{source_command('rm', source_id, project_id)}`."
        ),
        details={"source_id": source_id, "external_item_name": name},
        request_id=exc.request_id,
    ) from exc


def _filling(entry: Any) -> tuple[bool, Any, Any]:
    """``(filling, brought_in, matching_rows)`` of one ``notion_rows`` entry."""
    if not isinstance(entry, dict):
        return False, None, None
    return bool(entry.get("filling")), entry.get("brought_in"), entry.get("matching_rows")


def _database_label(database: Any, entry: Any) -> str:
    """A database by its title (``notion_rows[...].title``), its id when untitled."""
    title = entry.get("title") if isinstance(entry, dict) else None
    return str(title).strip() if title and str(title).strip() else str(database)


def _notion_rows(row: dict[str, Any]) -> list[tuple[str, str]]:
    """``(database title, "N rows match")`` per database of a Notion source, from its last sync.

    ``notion_rows`` (beakr-engine #3061) maps each database the source reads to
    ``{matching_rows, counted_at, brought_in, filling, title}``; it is null or absent for
    other sources and before a sync has counted. While ``filling`` (follow-up
    syncs are still bringing rows in, minutes apart) it reads "N of M rows
    brought in".
    """
    counts = row.get("notion_rows")
    if not isinstance(counts, dict):
        return []
    out = []
    for database, entry in counts.items():
        filling, brought_in, matching = _filling(entry)
        if filling and brought_in is not None:
            phrase = (
                f"{brought_in} of {matching} rows brought in"
                if matching is not None
                else f"{brought_in} rows brought in so far"
            )
            out.append((_database_label(database, entry), phrase))
            continue
        if matching is None:
            out.append((_database_label(database, entry), "not counted yet"))
            continue
        phrase = f"{matching} {'row matches' if matching == 1 else 'rows match'}"
        counted_at = entry.get("counted_at") if isinstance(entry, dict) else None
        out.append(
            (
                _database_label(database, entry),
                f"{phrase} (as of {counted_at})" if counted_at else phrase,
            )
        )
    return out


def _rows_field(row: dict[str, Any]) -> list[str]:
    """``notion_rows=<database>:<n>,...`` for a compact line.

    ``<database>`` is the database's title (its id when untitled; ``,`` and
    ``:`` in a title become spaces so the field still splits; ``--json`` keeps
    the ids). ``<n>`` is the matching count (``?`` when not counted); while the
    database is still filling in it is ``<brought in>/<matching>`` instead.
    """
    counts = row.get("notion_rows")
    if not isinstance(counts, dict) or not counts:
        return []
    parts = []
    for database, entry in counts.items():
        filling, brought_in, matching = _filling(entry)
        shown = "?" if matching is None else str(matching)
        if filling and brought_in is not None:
            shown = f"{brought_in}/{shown}"
        label = " ".join(
            _database_label(database, entry).replace(",", " ").replace(":", " ").split()
        )
        parts.append(f"{label}:{shown}")
    return [f"notion_rows={cell(','.join(parts), width=2000)}"]


#: A source's sync health (R10): when its syncs started failing, the latest
#: reason, and when one last succeeded. The engine sets ``failing_since`` while
#: every finished sync since the last success has failed.
SYNC_HEALTH_FIELDS = ("failing_since", "sync_failure_reason", "last_successful_sync_at")


def _failing_sentence(row: dict[str, Any]) -> str | None:
    """ "Syncs have failed since <when>: <why>. Last successful sync: <when>." or None.

    ``sync_status`` and ``last_sync_at`` are the latest attempt, so a source
    that fails every hour still looked freshly synced.
    """
    since = row.get("failing_since")
    if not since:
        return None
    reason = str(row.get("sync_failure_reason") or "").strip().rstrip(".")
    last_ok = row.get("last_successful_sync_at")
    return (
        f"Syncs have failed since {since}"
        + (f": {reason}." if reason else ".")
        + (f" Last successful sync: {last_ok}." if last_ok else " No sync has succeeded yet.")
    )


def _sync_cell(row: dict[str, Any]) -> str:
    """The Sync column: the latest sync's state, and since when syncs fail."""
    state = str(row.get("sync_status") or "never")
    since = row.get("failing_since")
    return f"{state}, failing since {str(since)[:10]}" if since else state


def _health_fields(row: dict[str, Any]) -> list[str]:
    """``failing_since=...`` and the rest, in a machine line, only when set."""
    return [f"{name}={cell(row[name], width=500)}" for name in SYNC_HEALTH_FIELDS if row.get(name)]


def _source_lines(rows: list[dict[str, Any]]) -> list[str]:
    return [
        "\t".join(
            [
                cell(r.get("id") or "-"),
                cell(r.get("external_item_name") or ""),
                f"provider={cell(r.get('provider') or '')}",
                f"type={cell(r.get('external_item_type') or '')}",
                f"knowledge={'on' if r.get('ingest_wiki') else 'off'}",
                f"sync={cell(r.get('sync_status') or 'never')}",
                *_health_fields(r),
                *_rows_field(r),
            ]
        )
        for r in rows
    ]


#: What ``ls`` says when a project has no sources, at a terminal and piped alike:
#: a piped run that printed nothing read as a failed call (e2e-local, 2026-11-21).
_NO_SOURCES = "No sources yet. Attach one with `beakr sources attach <link>`."


@app.command("ls")
def list_sources(project: str | None = _project_opt, json_out: bool = _json_opt) -> None:
    """List the project's sources: provider, item, Knowledge on or off, latest sync.

    A source whose syncs keep failing says since when, why, and when one last
    succeeded. A Notion source also shows, for each database it reads, how many
    rows matched its rules at the last sync.
    """
    project_id = _require_project(project)
    with get_sdk() as sdk:
        rows = sdk.project_sources.list(project_id)

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            console.print(_NO_SOURCES, markup=False, highlight=False)
            return
        print_table(
            ["ID", "Item", "Provider", "Knowledge", "Sync"],
            [
                [
                    str(r.get("id") or ""),
                    short(r.get("external_item_name"), 32),
                    f"{r.get('provider') or ''} {r.get('external_item_type') or ''}".strip(),
                    ("on" if r.get("ingest_wiki") else "off")
                    + (", nightly" if r.get("auto_sync_wiki") else ""),
                    _sync_cell(r),
                ]
                for r in data
            ],
            title=f"Sources ({len(data)})",
        )
        for r in data:
            failing = _failing_sentence(r)
            if failing:
                console.print(
                    f"{r.get('external_item_name') or r.get('id')} ({r.get('id')}): {failing}",
                    highlight=False,
                    markup=False,
                    soft_wrap=True,
                )
        counted = [(r, _notion_rows(r)) for r in data if _notion_rows(r)]
        if counted:
            console.print("Notion rows matching at the last sync:", markup=False)
        for r, databases in counted:
            console.print(
                f"  {r.get('external_item_name') or r.get('id')} ({r.get('id')})",
                highlight=False,
                markup=False,
                soft_wrap=True,
            )
            for database, phrase in databases:
                console.print(f"    {database}: {phrase}", highlight=False, markup=False)

    def lines(data: list[dict[str, Any]]) -> list[str]:
        return _source_lines(data) if data else [f"note: {_NO_SOURCES}"]

    emit(
        rows,
        json_out=json_out,
        render=render,
        lines=lines,
        hint=None if rows else f"beakr sources attach <link> -P {project_id}",
    )


@app.command("attach")
def attach(
    item: str | None = typer.Argument(
        None, help="The item's link, or its id with --provider, --type and --name."
    ),
    url: str | None = typer.Option(
        None, "--url", help="A Notion, Google Drive or Slack link: id, type, provider come from it."
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="notion, drive, slack, ... or a provider id (gdrive:nango). Read from --url.",
    ),
    item_type: str | None = typer.Option(
        None, "--type", help="file, folder, page, database, channel... (read from --url)."
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help=(
            "Display name (default: the item's title, else the id). Not with --scope-mode all, "
            'which Beakr names "All <provider>".'
        ),
    ),
    connector: str | None = typer.Option(
        None,
        "--connector",
        help="Connection to read it with (`beakr integrations ls`); needed if several.",
    ),
    path: str | None = typer.Option(None, "--path", help="Breadcrumb path shown with it."),
    knowledge: bool = typer.Option(
        False, "--knowledge/--no-knowledge", help="Build Knowledge from it (default: off)."
    ),
    auto_sync: bool | None = typer.Option(
        None,
        "--auto-sync/--no-auto-sync",
        help="Re-sync it nightly (default: on with --knowledge).",
    ),
    scope_mode: str | None = typer.Option(
        None,
        "--scope-mode",
        help="item (the default), or all: everything the connection can read (no item).",
    ),
    query: str | None = _query_opt,
    drive_q: str | None = _drive_q_opt,
    gmail_q: str | None = _gmail_q_opt,
    jql: str | None = _jql_opt,
    cql: str | None = _cql_opt,
    github_q: str | None = _github_q_opt,
    mode: str | None = typer.Option(None, "--mode", help=_MODE_HELP),
    guidance: str | None = typer.Option(
        None, "--guidance", help="What to pull from it and what to record or skip, in words."
    ),
    sync_now: bool = typer.Option(False, "--sync", help="Start a sync once it is attached."),
    preview: bool = typer.Option(
        False,
        "--preview",
        "--dry-run",
        help="Count what it would pull under the query and mode, with examples; attach nothing.",
    ),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Attach a connected item to the project (a person must do it where agents need review).

    \b
    beakr sources attach https://www.notion.so/acme/Assay-Handbook-0123...cdef --knowledge
    beakr sources attach --url https://drive.google.com/drive/folders/1AbC... --knowledge --sync
    beakr sources attach C01ABCDEF --provider slack --type channel --name "#assays"
    beakr sources attach --provider drive --scope-mode all
    beakr sources attach LAB --provider jira --type project --jql 'status = Done' --preview
    """
    scope_mode = _choice(scope_mode, SCOPE_MODES, "--scope-mode")
    mode = _choice(mode, PULL_MODES, "--mode")
    source_query = _query(
        query, drive_q=drive_q, gmail_q=gmail_q, jql=jql, cql=cql, github_q=github_q
    )
    if preview and sync_now:
        raise click.UsageError("--preview attaches nothing, so it cannot --sync.")
    if item and url:
        raise click.UsageError("Give the item as the argument or as --url, not both.")
    if item and item.strip().lower().startswith(("http://", "https://")):
        item, url = None, item
    view_id: str | None = None
    link_name: str | None = None

    if scope_mode == "all":
        if item or url:
            raise click.UsageError("--scope-mode all attaches everything: give no item or link.")
        if not provider:
            raise click.UsageError("--scope-mode all needs --provider.")
        if name:
            # The engine names an all-content source itself ("All Gmail") and drops
            # any other name; a --name that silently did nothing led a tester to
            # detach the wrong "All ..." source (2026-11-21).
            raise click.UsageError(
                "--name does not apply with --scope-mode all: Beakr names the source "
                '"All <provider>" (for example "All Gmail"). Drop --name; `beakr sources ls` '
                "shows each source's id to tell them apart."
            )
        slug = provider_slug(provider)
        item_id, kind = "__all__", item_type or "all"
        title = f"All {provider}"
    elif url:
        try:
            link = parse_source_link(url)
        except SourceLinkError as exc:
            raise typer.BadParameter(str(exc), param_hint="--url") from exc
        if provider and provider_slug(provider) != link.provider:
            raise click.UsageError(
                f"--provider {provider} does not match the link, which is {link.provider}."
            )
        slug, item_id = link.provider, link.item_id
        kind = item_type or link.item_type
        link_name = link.name
        if not kind:
            raise click.UsageError(
                "The link does not say whether it is a file or a folder: pass --type folder "
                "(Drive sources are folders)."
            )
        title = name or link.name or item_id
        if link.view_id and source_query is None:
            view_id = link.view_id
    else:
        missing = [
            flag
            for flag, value in (
                ("an item id", item),
                ("--provider", provider),
                ("--type", item_type),
            )
            if not value
        ]
        if missing:
            raise click.UsageError(
                f"Give the item's link (--url), or {', '.join(missing)} "
                "(with --name for its display name)."
            )
        slug, item_id, kind = provider_slug(str(provider)), str(item), str(item_type)
        title = name or item_id
    url_sent = url if url and scope_mode != "all" else None
    folder_source = FOLDER_SOURCE_NAMES.get(slug)
    if folder_source and scope_mode != "all" and str(kind).lower() == "file":
        # These connectors sync a source by listing it as a folder; a file
        # syncs nothing, and the engine refuses it (422 item_not_folder).
        raise click.UsageError(
            f"{folder_source} sources are folders. Attach the folder that holds this file, "
            "or upload the file to the project (`beakr files upload`)."
        )

    project_id = _require_project(project)
    _note_mode(mode, slug)
    sync_error: BeakrError | None = None
    with get_sdk() as sdk:
        keys = _query_keys(sdk) if source_query else None
        if source_query and slug != "notion:nango":
            _check_query(keys, source_query, provider=slug, item_type=kind)
        connector_id = connector or connector_for(sdk, project_id, slug)
        type_unsure = False
        if slug == "notion:nango" and scope_mode != "all" and not (item_type and name):
            # Ask Notion what the item is and what it is called: a database's
            # own link reads like a page's, and a view link names no title.
            found = notion_item(sdk, connector_id, item_id)
            if found is not None:
                kind = item_type or str(found["kind"])
                title = name or found["name"] or link_name or item_id
            else:
                # A link without ?v= reads as a page by default; only a view
                # link (or --type) says what the item is.
                type_unsure = not item_type and kind == "page"
        if source_query and slug == "notion:nango":
            _check_query(keys, source_query, provider=slug, item_type=kind, type_unsure=type_unsure)
        if preview:
            counted = sdk.project_sources.preview_item(
                project_id,
                provider=slug,
                connector_id=connector_id,
                external_item_id=item_id,
                external_item_type=kind,
                scope_mode=scope_mode,
                source_query=source_query,
                pull_mode=mode,
            )
        else:
            try:
                source = sdk.project_sources.attach(
                    project_id,
                    provider=slug,
                    connector_id=connector_id,
                    external_item_id=item_id,
                    external_item_type=kind,
                    external_item_name=title,
                    external_item_path=path,
                    external_item_url=url_sent,
                    ingest_wiki=knowledge,
                    auto_sync_wiki=auto_sync,
                    scope_mode=scope_mode,
                    source_query=source_query,
                    pull_mode=mode,
                    guidance=guidance,
                )
            except BeakrError as exc:
                if exc.status == 422 and (source_query is not None or mode is not None):
                    _add_fix(
                        exc,
                        _unsaved(exc, "Nothing was attached.")
                        + "`beakr sources attach ... --preview` checks the rules without "
                        "attaching.",
                    )
                if exc.status == 409 or (exc.status or 0) >= 500:
                    _refuse_duplicate(sdk, project_id, slug, item_id, exc)
                raise
            result: dict[str, Any] = dict(source or {})
            if sync_now and result.get("id"):
                try:
                    result["sync"] = sdk.project_sources.sync(project_id, str(result["id"]))
                except BeakrError as exc:
                    # Attached all the same: say so, with the id, before the refusal.
                    sync_error = exc
                    result["sync_error"] = error_object(exc, exit_code_for(exc), ("sources",))[
                        "error"
                    ]
    if preview:
        _emit_preview(counted, json_out)
        return

    source_id = result.get("id")
    # A sync of a source with Knowledge off is refused ("Wiki ingestion is not
    # enabled for this source", 400), so its next step is turning Knowledge on.
    if "sync" in result:
        next_step = source_command("status", source_id, project_id)
    elif result.get("ingest_wiki"):
        next_step = source_command("sync", source_id, project_id)
    else:
        next_step = source_command("edit", source_id, project_id, "--knowledge")

    def render(data: dict[str, Any]) -> None:
        console.print(
            f"Attached {data.get('external_item_name')} ({data.get('provider')} "
            f"{data.get('external_item_type')}) as {source_id}; Knowledge "
            f"{'on' if data.get('ingest_wiki') else 'off'}.",
            highlight=False,
            markup=False,
        )
        if "sync" in data:
            _render_sync(data["sync"])
        elif data.get("ingest_wiki"):
            err_console.print(f"Start it: {next_step}", markup=False, soft_wrap=True)
        else:
            err_console.print(
                f"Nothing is read until Knowledge is on: {next_step}",
                markup=False,
                soft_wrap=True,
            )
        if view_id:
            # Unwrapped, so the command stays one copyable line.
            only_view = json.dumps({"notion_view": view_id})
            err_console.print(
                f"The link opened one saved view of the database ({view_id}). To pull only "
                f"its rows: {source_command('edit', source_id, project_id, '--query', only_view)}",
                markup=False,
                soft_wrap=True,
            )

    emit(
        result,
        json_out=json_out,
        render=render,
        lines=lambda data: _source_lines([data]),
        hint=next_step,
    )
    if sync_error is not None:
        err_console.print(
            f"Attached {source_id}, but the sync did not start: "
            f"{describe_error(sync_error, ('sources',))}",
            highlight=False,
            markup=False,
        )
        raise typer.Exit(exit_code_for(sync_error))


@app.command("edit")
def edit(
    source_id: str = _source_arg,
    knowledge: bool | None = typer.Option(
        None, "--knowledge/--no-knowledge", help="Build Knowledge from it, or stop."
    ),
    auto_sync: bool | None = typer.Option(
        None, "--auto-sync/--no-auto-sync", help="Re-sync it nightly, or stop."
    ),
    query: str | None = _query_opt,
    drive_q: str | None = _drive_q_opt,
    gmail_q: str | None = _gmail_q_opt,
    jql: str | None = _jql_opt,
    cql: str | None = _cql_opt,
    github_q: str | None = _github_q_opt,
    clear_query: bool = typer.Option(False, "--clear-query", help="Drop the query."),
    mode: str | None = typer.Option(None, "--mode", help=_MODE_HELP),
    guidance: str | None = typer.Option(
        None, "--guidance", help="What to pull and what to record or skip, in words."
    ),
    clear_guidance: bool = typer.Option(False, "--clear-guidance", help="Drop the guidance."),
    check: bool = typer.Option(
        True,
        "--check/--no-check",
        help="After changing the query or mode, count what the saved rules match (default: on).",
    ),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Change a source: Knowledge, nightly sync, query, mode, guidance. It does not sync.

    After a change to the query or mode it counts what the saved rules match (as
    `beakr sources preview` does) and warns when nothing would come in: no item
    matches, every database under a Notion page is left out, or the provider
    rejects the query. `beakr sources preview SRC --query ...` counts a query
    before saving it.
    """
    mode = _choice(mode, PULL_MODES, "--mode")
    source_query = _query(
        query, drive_q=drive_q, gmail_q=gmail_q, jql=jql, cql=cql, github_q=github_q
    )
    clears = (
        ("source_query", "--query", source_query, clear_query),
        ("guidance", "--guidance", guidance, clear_guidance),
    )
    for _field, flag, value, cleared in clears:
        if value is not None and cleared:
            raise click.UsageError(f"Use {flag} or --clear-{flag[2:]}, not both.")
    clear = [field for field, _flag, _value, cleared in clears if cleared]
    changes = (knowledge, auto_sync, source_query, mode, guidance)
    if all(value is None for value in changes) and not clear:
        raise click.UsageError(
            "Nothing to change: pass --knowledge/--no-knowledge, --auto-sync/--no-auto-sync, "
            "--query, --mode or --guidance (or --clear-query, --clear-guidance)."
        )
    project_id = _require_project(project)
    rules_changed = source_query is not None or clear_query or mode is not None
    counted: dict[str, Any] | None = None
    count_error: BeakrError | None = None
    with get_sdk() as sdk:
        row = _attached_row(sdk, project_id, source_id)
        _check_attached(sdk, project_id, source_id, source_query, mode, row=row)
        try:
            source = sdk.project_sources.update(
                project_id,
                source_id,
                ingest_wiki=knowledge,
                auto_sync_wiki=auto_sync,
                source_query=source_query,
                pull_mode=mode,
                guidance=guidance,
                clear=clear,
            )
        except BeakrError as exc:
            if exc.status == 422 and (source_query is not None or mode is not None):
                _add_fix(
                    exc,
                    _unsaved(exc, "Nothing was saved; the source keeps its rules.")
                    + f"`{source_command('preview', source_id, project_id)} --query ...` checks "
                    "a query without saving it.",
                )
            raise
        result = _with_sync_state(dict(source or {}), row)
        if rules_changed and check:
            try:
                counted = dict(sdk.project_sources.preview(project_id, source_id) or {})
            except NotFoundError:
                counted = None  # an engine without previews
            except BeakrError as exc:
                count_error = exc
    if counted is not None:
        result["preview"] = counted

    def render(data: dict[str, Any]) -> None:
        console.print(
            f"Updated {data.get('external_item_name')} ({source_id}): Knowledge "
            f"{'on' if data.get('ingest_wiki') else 'off'}, nightly sync "
            f"{'on' if data.get('auto_sync_wiki') else 'off'}.",
            highlight=False,
            markup=False,
        )
        if counted is not None and not _edit_warnings(counted):
            console.print(_matching(counted), highlight=False, markup=False)

    def lines(data: dict[str, Any]) -> list[str]:
        out = _source_lines([data])
        if counted is not None:
            # Piped, the count rides on the source's line (e2e-live H11 saw none).
            capped = ">=" if counted.get("count_capped") else ""
            out[0] += f"\tmatching={capped}{int(counted.get('count') or 0)}"
        return out

    emit(
        result,
        json_out=json_out,
        render=render,
        lines=lines,
        hint=source_command("sync", source_id, project_id) if knowledge else None,
    )
    for warning in _edit_warnings(counted) if counted is not None else []:
        err_console.print(f"Warning: {warning}", highlight=False, markup=False, soft_wrap=True)
    if count_error is not None:
        err_console.print(
            "Saved, but the new rules could not be counted: "
            f"{describe_error(count_error, ('sources',))} Check with: beakr sources preview "
            f"{source_id}",
            highlight=False,
            markup=False,
        )


#: Fields of a listed source that the engine's PATCH answer leaves empty: the
#: latest sync is read from the sync jobs only when sources are listed, so an
#: edit's own answer said sync=never for a source that had synced (e2e-live H08).
_SYNC_STATE = ("sync_status", "last_sync_at", "notion_rows")


def _with_sync_state(source: dict[str, Any], row: dict[str, Any] | None) -> dict[str, Any]:
    """``source`` with the latest sync the listing reports, where its own answer has none."""
    for field in _SYNC_STATE:
        if source.get(field) is None and row and row.get(field) is not None:
            source[field] = row[field]
    return source


def _unsaved(exc: BeakrError, sentence: str) -> str:
    """``sentence`` and a space, unless the engine's message already says nothing was saved.

    The engine's refusal of a Notion rule naming a property no database has
    (beakr-engine #3077) ends "Nothing was saved."; the CLI does not repeat it.
    """
    return "" if "nothing was saved" in str(exc.message).lower() else f"{sentence} "


def _add_fix(exc: BeakrError, text: str) -> None:
    """Add ``text`` to the fix an error prints (after the engine's own, if any)."""
    exc.fix = f"{exc.fix} {text}" if exc.fix else text


def _matching(counted: dict[str, Any]) -> str:
    count = int(counted.get("count") or 0)
    capped = "at least " if counted.get("count_capped") else ""
    noun = "item matches" if count == 1 and not capped else "items match"
    return f"Under the saved rules {capped}{count} {noun} (`beakr sources preview` shows them)."


def _edit_warnings(counted: dict[str, Any]) -> list[str]:
    """What an edit's preview says is wrong with the saved rules: nothing would come in."""
    if counted.get("provider_status") == "rejected":
        return [
            f"the provider rejects the saved query: {counted.get('message') or 'no reason given'}."
            " The source cannot sync until the query is fixed."
        ]
    breakdown = [group for group in counted.get("breakdown") or [] if isinstance(group, dict)]
    if breakdown and all(group.get("excluded_reason") for group in breakdown):
        left_out = "; ".join(
            f"{group.get('name') or group.get('id')} ({group['excluded_reason']})"
            for group in breakdown
        )
        return [f"the saved rules leave out every database: {left_out}."]
    if int(counted.get("count") or 0) == 0 and not counted.get("count_capped"):
        return ["no item matches the saved rules, so a sync brings nothing in."]
    return []


def _attached_row(sdk: Any, project_id: str, source_id: str) -> dict[str, Any] | None:
    """The source as ``ls`` lists it (with its latest sync), or None when not listed."""
    return next(
        (r for r in sdk.project_sources.list(project_id) if str(r.get("id")) == source_id), None
    )


_UNREAD: Any = object()


def _check_attached(
    sdk: Any,
    project_id: str,
    source_id: str,
    source_query: dict[str, Any] | None,
    mode: str | None,
    row: dict[str, Any] | None = _UNREAD,
) -> None:
    """Before an edit or preview: check the query against the attached source's provider
    and item type (:func:`_check_query`), and note a --mode that does nothing there.

    ``row`` is the listed source when the caller already read it."""
    keys = _query_keys(sdk) if source_query else None
    if keys is None and not mode:
        return
    if row is _UNREAD:
        row = _attached_row(sdk, project_id, source_id)
    if keys is not None:
        # Not listed (another project's, or gone): the key alone; the engine says the rest.
        _check_query(
            keys,
            source_query,
            provider=row.get("provider") if row else None,
            item_type=row.get("external_item_type") if row else None,
        )
    _note_mode(mode, row.get("provider") if row else None)


@app.command("preview")
def preview(
    source_id: str = _source_arg,
    query: str | None = _query_opt,
    drive_q: str | None = _drive_q_opt,
    gmail_q: str | None = _gmail_q_opt,
    jql: str | None = _jql_opt,
    cql: str | None = _cql_opt,
    github_q: str | None = _github_q_opt,
    no_query: bool = typer.Option(False, "--no-query", help="Count it with no query at all."),
    mode: str | None = typer.Option(None, "--mode", help=_MODE_HELP),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Count what a source would pull under its query, or a new one, with examples; saves nothing.

    Without a query flag it counts under the saved query. Prints the count ("at
    least N" when the preview stopped early), up to 10 matching items, a count per
    database under a Notion page, and the provider's reason when it rejects the
    query (exit 1: a rejected query cannot sync).
    """
    mode = _choice(mode, PULL_MODES, "--mode")
    source_query = _query(
        query, drive_q=drive_q, gmail_q=gmail_q, jql=jql, cql=cql, github_q=github_q
    )
    if no_query and source_query is not None:
        raise click.UsageError("Use a query or --no-query, not both.")
    project_id = _require_project(project)
    with get_sdk() as sdk:
        _check_attached(sdk, project_id, source_id, source_query, mode)
        # Neither a query nor --no-query: the source's saved query is counted.
        given: dict[str, Any] = (
            {"source_query": None if no_query else source_query} if no_query or source_query else {}
        )
        counted = sdk.project_sources.preview(project_id, source_id, pull_mode=mode, **given)
    _emit_preview(counted, json_out)


@app.command("keys")
def keys(
    provider: str | None = typer.Option(
        None, "--provider", help="Only the keys this provider takes (notion, jira, gmail...)."
    ),
    json_out: bool = _json_opt,
) -> None:
    """The source query keys --query takes: which providers and items take each, and an example."""
    with get_sdk() as sdk:
        # A credential refused the listing hears the refusal, not "not listed".
        listing = _query_keys(sdk, refused=False)
    if listing is None:
        raise click.ClickException(
            "This Beakr does not list source query keys yet; --query is sent as given."
        )
    slug = provider_slug(provider) if provider else None
    rows = [row for row in listing if slug is None or slug in (row.get("providers") or [])]

    def example(row: dict[str, Any]) -> str:
        value = row.get("example")
        return value if isinstance(value, str) else json.dumps(value)

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            err_console.print(f"No source query key applies to {provider}.", markup=False)
            return
        for row in data:
            items = row.get("item_types")
            excluded = row.get("excluded_item_types")
            applies = ", ".join(row.get("providers") or [])
            if items:
                applies += f" ({' or '.join(items)} only)"
            elif excluded:
                applies += f" (not a {' or '.join(excluded)})"
            console.print(f"{row.get('key')}  [{row.get('kind')}]  {applies}", markup=False)
            console.print(
                f"  --query '{json.dumps({row.get('key'): row.get('example')})}'",
                highlight=False,
                markup=False,
                soft_wrap=True,
            )
            if row.get("fields"):
                console.print(f"  fields: {', '.join(row['fields'])}", markup=False)
            if row.get("note"):
                console.print(f"  {row['note']}", highlight=False, markup=False)

    def lines(data: list[dict[str, Any]]) -> list[str]:
        return [
            "\t".join(
                [
                    cell(row.get("key") or "-"),
                    f"kind={row.get('kind')}",
                    f"providers={','.join(row.get('providers') or [])}",
                    f"example={cell(example(row), width=200)}",
                ]
            )
            for row in data
        ]

    emit(rows, json_out=json_out, render=render, lines=lines)


@app.command("rm")
def remove(
    source_id: str = _source_arg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Detach a source from the project; what Knowledge already took from it stays.

    Detaching also stops the source's running sync and cancels its compiles
    still queued.
    """
    project_id = _require_project(project)
    confirm_or_require_yes(f"Detach source {source_id} from the project?", yes=yes)
    with get_sdk() as sdk:
        sdk.project_sources.remove(project_id, source_id)
    emit(
        {"id": source_id, "project_id": project_id, "removed": True},
        json_out=json_out,
        render=lambda _: err_console.print(f"Detached {source_id}.", markup=False),
        json_when_piped=False,
    )


def _render_sync(data: dict[str, Any]) -> None:
    job = data.get("job_id")
    state = data.get("status") or "queued"
    console.print(f"Sync {state}{f' (job {job})' if job else ''}.", highlight=False, markup=False)
    if data.get("message"):
        err_console.print(str(data["message"]), markup=False)


@app.command("sync")
def sync(
    source_id: str = _source_arg,
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Start pulling a source into Knowledge now; `beakr sources status` follows it."""
    project_id = _require_project(project)
    with get_sdk() as sdk:
        result = sdk.project_sources.sync(project_id, source_id)
    emit(
        result,
        json_out=json_out,
        render=_render_sync,
        hint=source_command("status", source_id, project_id),
    )


# -- recompile (R4) -----------------------------------------------------------------------
#
# Send a source's tracked items to Knowledge again, in any state (POST .../recompile,
# engine #3298). Through the SDK's ``request``: the released beakr-sdk predates a method.

#: What the engine says about the sync a recompile starts.
_RECOMPILE_SYNC = {
    "started": "A sync reading them has started",
    "waiting_for_running_sync": "A sync is already running; a new one reads them when it ends",
    "not_started": "No sync was started",
}


def recompile_path(project_id: str, source_id: str) -> str:
    return f"/v1/projects/{project_id}/sources/{source_id}/recompile"


def _external_ids(items: list[str] | None) -> list[str]:
    """``--items a,b --items c`` as ``["a", "b", "c"]``, in order, without repeats."""
    seen: dict[str, None] = {}
    for value in items or []:
        for part in value.split(","):
            if part.strip():
                seen.setdefault(part.strip(), None)
    return list(seen)


def _recompile_lines(data: dict[str, Any], source_id: str) -> list[str]:
    mode = data.get("mode") or "retry"
    requested = int(data.get("requested") or 0)
    verb = "Would send" if data.get("dry_run") else "Sent"
    lines = [f"{verb} {requested} item(s) of {source_id} to Knowledge again ({mode})."]
    left = int(data.get("in_knowledge_left") or 0)
    if left:
        lines.append(
            f"{left} already in Knowledge at their current version were left alone "
            "(--full sends them too)."
        )
    in_progress = int(data.get("in_progress_left") or 0)
    if in_progress:
        lines.append(
            f"{in_progress} still on their way into Knowledge were left out "
            "(their compile is queued or running): send them again once they finish."
        )
    over = int(data.get("over_cap") or 0)
    if over:
        lines.append(f"{over} more are past the 500-item limit: send them in a later request.")
    missing = [str(item) for item in data.get("not_found") or []]
    if missing:
        lines.append(f"Not tracked by this source, nothing sent: {', '.join(missing)}.")
    if not data.get("dry_run"):
        sync = _RECOMPILE_SYNC.get(str(data.get("sync")), str(data.get("sync") or ""))
        job = data.get("sync_job_id")
        lines.append(f"{sync}" + (f" (job {job})." if job else "."))
    return lines


@app.command("recompile")
def recompile(
    source_id: str = _source_arg,
    items: list[str] | None = typer.Option(
        None,
        "--items",
        help="Items to send again, by external_id (`beakr sources tracked`); comma-separated "
        "or repeated.",
    ),
    all_items: bool = typer.Option(False, "--all", help="Every item the source tracks (500 max)."),
    full: bool = typer.Option(
        False,
        "--full",
        help="Also send items already in Knowledge and read each whole document again.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Count what would be sent; send nothing."
    ),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Send a source's items to Knowledge again, in any state, and sync them now.

    \b
    Without --full only items not in Knowledge at their current version go
    (pending, failed, skipped). --full sends items already in Knowledge too and
    has each whole document read again, for example after the source's guidance
    changed. Items still on their way into Knowledge are left out and counted.
    Anyone who can edit the project may; one request per source every ten
    minutes, up to 500 items each, and none while a sync of the source runs. It
    is not held by `beakr sources sync`'s hourly cooldown.

    \b
    beakr sources recompile <id> --items conn:t1,conn:t2
    beakr sources recompile <id> --all --full --dry-run
    """
    external_ids = _external_ids(items)
    if bool(external_ids) == all_items:
        raise click.UsageError("Name the items with --items, or pass --all (not both).")
    project_id = _require_project(project)
    body: dict[str, Any] = {"mode": "full" if full else "retry", "dry_run": dry_run}
    if all_items:
        body["all"] = True
    else:
        body["external_ids"] = external_ids
    with get_sdk() as sdk:
        data = dict(sdk.request("POST", recompile_path(project_id, source_id), json=body) or {})

    def render(answer: dict[str, Any]) -> None:
        for line in _recompile_lines(answer, source_id):
            console.print(line, highlight=False, markup=False, soft_wrap=True)

    def lines(answer: dict[str, Any]) -> list[str]:
        fields = [
            cell(source_id),
            f"mode={answer.get('mode') or body['mode']}",
            f"dry_run={'true' if answer.get('dry_run') else 'false'}",
            f"requested={int(answer.get('requested') or 0)}",
            f"in_knowledge_left={int(answer.get('in_knowledge_left') or 0)}",
            f"in_progress_left={int(answer.get('in_progress_left') or 0)}",
            f"over_cap={int(answer.get('over_cap') or 0)}",
            f"not_found={len(answer.get('not_found') or [])}",
            f"sync={answer.get('sync') or ''}",
        ]
        if answer.get("sync_job_id"):
            fields.append(f"sync_job_id={answer['sync_job_id']}")
        return ["\t".join(fields)]

    emit(
        data,
        json_out=json_out,
        render=render,
        lines=lines,
        hint=None if data.get("dry_run") else source_command("status", source_id, project_id),
    )


#: Sync states in which a sync is still going: ``cancel-sync`` can stop it.
#: The engine lists a queued or running sync as "syncing" (``_map_sync_job_status``).
RUNNING_SYNC_STATES = frozenset({"syncing", "running", "queued"})


def _sync_running(state: Any) -> bool:
    return str(state or "").lower() in RUNNING_SYNC_STATES


def _queued_compiles(sdk: Any, project_id: str, source_id: str) -> dict[str, Any] | None:
    """The source's Knowledge compile queue, or None from an engine that does not count it."""
    try:
        answer = cancels.compile_queue(sdk, project_id, attachment_id=source_id)
    except NotFoundError:
        return None
    return dict(answer) if isinstance(answer, dict) else None


@app.command("status")
def status(
    source_id: str | None = typer.Argument(
        None, help="Source id from `beakr sources ls`; without one, every running sync."
    ),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """The latest sync of a source, or which of the project's sources are syncing now.

    \b
    With a source: its sync state (syncing, cancelling, cancelled, completed,
    failed), since when its syncs keep failing and why (and when one last
    succeeded), files done, failed (and why) and skipped by a cancel, how a cancel
    went, what became of the items it brought in (ingested, skipped, pending,
    failed) and why any were set aside, and how many of its Knowledge
    compiles are queued and running. A running sync can be stopped with
    `beakr sources cancel-sync`.
    Without one: the sources whose sync is running (exit 0, also when none is).
    """
    project_id = _require_project(project)
    if source_id is None:
        _running_syncs(project_id, json_out)
        return
    with get_sdk() as sdk:
        result = dict(sdk.project_sources.status(project_id, source_id) or {})
        queue = _queued_compiles(sdk, project_id, source_id)
        # The latest sync's state says nothing of the ones before it: whether
        # syncs keep failing is on the listed source (R10).
        row = _attached_row(sdk, project_id, source_id) or {}
    if queue is not None:
        result["compiles"] = queue
    for name in SYNC_HEALTH_FIELDS:
        if row.get(name) is not None:
            result[name] = row[name]

    def render(data: dict[str, Any]) -> None:
        state = data.get("status")
        if not state:
            console.print(f"{source_id} has not synced yet.", markup=False)
        else:
            skipped = int(data.get("files_cancelled") or 0)
            console.print(
                f"Sync {state}: {data.get('files_completed', 0)}/{data.get('files_total', 0)} "
                f"files done, {data.get('files_failed', 0)} failed"
                + (f", {skipped} skipped by the cancel." if skipped else "."),
                highlight=False,
                markup=False,
            )
            failing = _failing_sentence(data)
            if failing:
                console.print(failing, highlight=False, markup=False, soft_wrap=True)
            stop = data.get("cancel")
            if isinstance(stop, dict) and stop:
                by = f" by {stop['by_name']}" if stop.get("by_name") else ""
                at = f" at {stop['at']}" if stop.get("at") else ""
                console.print(
                    f"{stop.get('summary') or 'Cancelled'}{by}{at}.",
                    highlight=False,
                    markup=False,
                )
            outcomes = data.get("outcomes")
            if isinstance(outcomes, dict) and outcomes:
                # What became of each item, Knowledge's own verdicts included; the
                # failed deliveries are among its items.
                for line in _outcome_lines(outcomes, source_id, project_id):
                    console.print(line, highlight=False, markup=False, soft_wrap=True)
            else:
                failed = [f for f in data.get("files") or [] if f.get("outcome") == "failed"]
                for entry in failed:
                    console.print(
                        f"  {entry.get('name')}  {entry.get('reason') or ''}",
                        highlight=False,
                        markup=False,
                    )
        if queue is not None:
            for line in cancels.queue_lines(queue, "for this source"):
                console.print(line, highlight=False, markup=False)
        if _sync_running(state):
            err_console.print(
                f"Stop it: {source_command('cancel-sync', source_id, project_id)}",
                markup=False,
                soft_wrap=True,
            )

    def lines(data: dict[str, Any]) -> list[str]:
        fields = [
            cell(source_id),
            str(data.get("status") or "never"),
            f"files_total={int(data.get('files_total') or 0)}",
            f"files_completed={int(data.get('files_completed') or 0)}",
            f"files_failed={int(data.get('files_failed') or 0)}",
        ]
        if data.get("files_cancelled") is not None:
            fields.append(f"files_cancelled={int(data.get('files_cancelled') or 0)}")
        fields += _health_fields(data)
        outcomes = data.get("outcomes")
        if isinstance(outcomes, dict):
            counts = outcomes.get("counts") or {}
            fields += [
                f"items_{name}={int(counts.get(name) or 0)}"
                for name in connected_sources.OUTCOME_ORDER
            ]
        if queue is not None:
            fields += [
                f"compiles_queued={int(queue.get('queued') or 0)}",
                f"compiles_running={int(queue.get('running') or 0)}",
            ]
        return ["\t".join(fields)]

    emit(
        result,
        json_out=json_out,
        render=render,
        lines=lines,
        hint=source_command("cancel-sync", source_id, project_id)
        if _sync_running(result.get("status"))
        else None,
    )


#: Items ``status`` names by line; the rest are counted, and ``tracked`` lists them.
STATUS_ITEMS_SHOWN = connected_sources.STATUS_ITEMS_SHOWN


def _item_reason(item: dict[str, Any]) -> str:
    code = item.get("reason_code")
    reason = str(item.get("reason") or "").strip()
    if reason and code:
        return f"{reason} ({code})"
    return reason or str(code or "")


def _outcome_lines(outcomes: dict[str, Any], source_id: str, project_id: str) -> list[str]:
    """What became of the items one sync brought in, for ``status``."""
    counts = outcomes.get("counts") or {}
    parts = [
        f"{counts[name]} {name}" for name in connected_sources.OUTCOME_ORDER if counts.get(name)
    ]
    if not parts:
        return ["Items: none brought in by this sync yet."]
    lines = [f"Items: {', '.join(parts)}."]
    reasons = [
        f"{entry.get('reason_code') or 'no reason recorded'} {entry.get('count')}"
        for entry in outcomes.get("reasons") or []
        if isinstance(entry, dict)
    ]
    if reasons:
        lines.append(f"Why: {', '.join(reasons)}.")
    listed = [
        item
        for item in outcomes.get("items") or []
        if isinstance(item, dict) and item.get("outcome") != "ingested"
    ][:STATUS_ITEMS_SHOWN]
    for item in listed:
        reason = _item_reason(item)
        lines.append(
            f"  {item.get('title')}  [{item.get('outcome')}]" + (f"  {reason}" if reason else "")
        )
    set_aside = sum(
        int(counts.get(name) or 0) for name in connected_sources.OUTCOME_ORDER if name != "ingested"
    )
    if set_aside > len(listed):
        lines.append(f"Every item and why: {source_command('tracked', source_id, project_id)}")
    return lines


#: ``--state`` values, as the tracked-items list takes them.
TRACKED_STATES = ("pending", "ingested", "partial", "skipped", "failed", "removed")
#: The most items one request of ``tracked`` asks for (the route's page maximum).
TRACKED_PAGE_MAX = 200
#: Items per page when ``--limit`` is not given.
TRACKED_PAGE_DEFAULT = 50


def tracked_next_page(
    source_id: str,
    project_id: str,
    cursor: str,
    *,
    state: str | None,
    reason_code: str | None,
    search: str | None,
    since: str | None,
    limit: int,
) -> str:
    """The ``beakr sources tracked`` command for the page after this one.

    The cursor carries only the position: each page must be asked with the same
    filters, or the route lists the next rows of the whole source (r108 sweep,
    26 Nov 2026: the hint dropped ``--state``/``--search`` and ``-P``, so it
    failed outside a bound directory and, with ``-P`` added, paged every item).
    """
    flags: list[str] = []
    for flag, value in (
        ("--state", state),
        ("--reason-code", reason_code),
        ("--search", search),
        ("--since", since),
    ):
        if value is not None:
            flags += [flag, value]
    if limit != TRACKED_PAGE_DEFAULT:
        flags += ["--limit", str(limit)]
    return source_command("tracked", source_id, project_id, *flags, "--cursor", cursor)


def _tracked_params(
    *,
    state: str | None,
    reason_code: str | None,
    search: str | None,
    since: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    for name, value in (
        ("state", state),
        ("reason_code", reason_code),
        ("search", search),
        ("since", since),
    ):
        text = str(value or "").strip()
        if text:
            params[name] = text
    return params


@app.command("tracked")
def tracked(
    source_id: str = _source_arg,
    skipped: bool = typer.Option(
        False, "--skipped", help="Only items set aside (same as --state skipped)."
    ),
    state: str | None = typer.Option(
        None,
        "--state",
        help="Only items in this state: pending, ingested, partial, skipped, failed or removed.",
    ),
    reason_code: str | None = typer.Option(
        None,
        "--reason-code",
        help="Only skipped, failed or partial items with this reason, e.g. nothing_to_add, "
        "compile_failed, compile_partial.",
    ),
    search: str | None = typer.Option(
        None, "--search", help="Only items whose title holds this text."
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        help="Only items synced at or after this time (ISO 8601), e.g. a sync's start.",
    ),
    limit: int = typer.Option(
        TRACKED_PAGE_DEFAULT,
        "--limit",
        "-n",
        min=1,
        max=TRACKED_PAGE_MAX,
        help="Items per page (1-200).",
    ),
    cursor: str | None = typer.Option(None, "--cursor", help="Continue from a page's next_cursor."),
    all_pages: bool = typer.Option(False, "--all", help="Follow every page."),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """The items a source tracks, what became of each, and why skipped ones were set aside.

    \b
    Each item is ingested (read into Knowledge), partial (in Knowledge with some
    planned changes not applied: compile_partial means the next sync retries
    them, compile_not_converging that they could not be applied), pending (not
    read yet), skipped (set aside, with the reason: a blocked file type, a file
    too large, or Knowledge read it and found nothing to add), failed (nothing
    of its version reached Knowledge, with the reason: compile_failed is sent
    again by the next sync, import_gave_up when a history import stopped trying
    it, retry_gave_up when Beakr stopped sending a stuck item again) or removed
    (gone from the source). A pending item says why it waits and when Beakr
    tries next. Counts cover the whole source; the filters narrow the list.
    """
    state = _choice(state, TRACKED_STATES, "--state")
    if skipped:
        if state not in (None, "skipped"):
            raise click.UsageError("--skipped is --state skipped; give one of them.")
        state = "skipped"
    project_id = _require_project(project)
    pages: list[dict[str, Any]] = []
    next_cursor = cursor
    with get_sdk() as sdk:
        while True:
            params = _tracked_params(
                state=state,
                reason_code=reason_code,
                search=search,
                since=since,
                limit=limit,
                cursor=next_cursor,
            )
            page = connected_sources.tracked_items_page(sdk, project_id, source_id, params)
            pages.append(dict(page or {}))
            next_cursor = pages[-1].get("next_cursor") if pages[-1].get("has_more") else None
            if not (all_pages and next_cursor):
                break
    result = dict(pages[-1])
    result["data"] = [row for page in pages for row in page.get("data") or []]
    if all_pages:
        result["next_cursor"] = None
        result["has_more"] = False
    next_page = (
        tracked_next_page(
            source_id,
            project_id,
            str(result["next_cursor"]),
            state=state,
            reason_code=reason_code,
            search=search,
            since=since,
            limit=limit,
        )
        if result.get("has_more") and result.get("next_cursor")
        else None
    )

    def render(data: dict[str, Any]) -> None:
        counts = data.get("counts") or {}
        total = int(counts.get("total") or 0)
        states = ", ".join(
            f"{int(counts.get(name) or 0)} {name}" for name in _tracked_count_names(counts)
        )
        console.print(
            f"{total} tracked item(s) from {data.get('provider') or 'this source'}: {states}.",
            highlight=False,
            markup=False,
        )
        matched = data.get("matched")
        if isinstance(matched, int) and matched != total:
            console.print(f"{matched} match the filters.", highlight=False, markup=False)
        rows = data.get("data") or []
        if not rows:
            console.print("No tracked items match.", markup=False)
        for row in rows:
            reason = _item_reason(row)
            # The state is the newest version's; an earlier one may still be in Knowledge.
            older = "  (an older version is in Knowledge)" if row.get(_OLDER_VERSION) else ""
            unfinished = row.get("unfinished_changes")
            if isinstance(unfinished, int) and unfinished > 0:
                older += f"  ({unfinished} planned change(s) not applied)"
            # Why a pending (or retried partial) item waits, and when it goes next.
            waiting = _pending_note(row)
            if waiting:
                older += f"  {waiting}"
            console.print(
                f"  {row.get('title')}  [{row.get('state')}]{older}"
                + (f"  {reason}" if reason else ""),
                highlight=False,
                markup=False,
                soft_wrap=True,
            )
        if next_page:
            err_console.print(f"More: {next_page} (or --all)", markup=False, soft_wrap=True)

    def lines(data: dict[str, Any]) -> list[str]:
        return [_tracked_line(row) for row in data.get("data") or []] or [
            "note: No tracked items match."
        ]

    emit(
        result,
        json_out=json_out,
        render=render,
        lines=lines,
        hint=next_page,
    )


#: A tracked item whose earlier version is in Knowledge while its newest one is
#: pending, failed or skipped (being updated, or the update failed).
_OLDER_VERSION = "older_version_in_knowledge"


def _tracked_line(row: dict[str, Any]) -> str:
    """One tracked item piped: external_id, state, reason_code and title, then what the
    terminal view adds (why a pending item waits and its next try, unfinished changes,
    an older version in Knowledge) as ``key=value`` columns, present only when set."""
    columns = [
        cell(row.get("external_id") or ""),
        str(row.get("state") or ""),
        cell(row.get("reason_code") or "-"),
        cell(row.get("title") or ""),
    ]
    if row.get("pending_reason"):
        columns.append(f"pending_reason={cell(row['pending_reason'])}")
    if row.get("pending_reason_text"):
        columns.append(f"pending_reason_text={cell(row['pending_reason_text'], width=300)}")
    if row.get("next_attempt_at"):
        columns.append(f"next_attempt_at={cell(row['next_attempt_at'])}")
    unfinished = row.get("unfinished_changes")
    if isinstance(unfinished, int) and not isinstance(unfinished, bool) and unfinished > 0:
        columns.append(f"unfinished_changes={unfinished}")
    if row.get(_OLDER_VERSION):
        columns.append(f"{_OLDER_VERSION}=true")
    return "\t".join(columns)


def _pending_note(row: dict[str, Any]) -> str:
    """Why a pending item waits and when Beakr tries next (engine #3323), or ``""``."""
    text = str(row.get("pending_reason_text") or "").strip().rstrip(".")
    if not text and row.get("pending_reason"):
        text = str(row["pending_reason"]).replace("_", " ")
    if not text:
        return ""
    when = row.get("next_attempt_at")
    return f"({text}" + (f"; next attempt {when}" if when else "") + ")"


def _tracked_count_names(counts: dict[str, Any]) -> tuple[str, ...]:
    """The states ``tracked`` counts: ``partial`` and ``failed`` where the engine counts them."""
    order = ("ingested", "partial", "skipped", "failed", "pending", "removed")
    optional = {"partial", "failed"}
    return tuple(name for name in order if name not in optional or name in counts)


# -- history import status (R6) ------------------------------------------------------------
#
# Whether a source takes a history import, whether one is waiting or running, and how the
# last one ended (GET .../history-import/status, engine #3292). Read through the SDK's
# ``request``: the released beakr-sdk predates a method for it.

#: The counts of an import's outcome, in the order they are printed.
IMPORT_COUNTS = ("in_knowledge", "skipped", "failed", "left", "removed")
#: Of ``in_knowledge``: partly compiled items being finished, and those with changes
#: that could not be applied (engine #3311); printed and piped when the engine sends them.
IMPORT_PARTIAL_COUNTS = ("finishing", "partly_applied")


def history_import_path(project_id: str, source_id: str) -> str:
    return f"/v1/projects/{project_id}/sources/{source_id}/history-import/status"


def _reason_counts(rows: Any, code: str = "reason_code") -> str:
    return ", ".join(
        f"{row.get(code)} {int(row.get('count') or 0)}"
        for row in rows or []
        if isinstance(row, dict) and row.get(code)
    )


def _outcome_text(outcome: dict[str, Any]) -> list[str]:
    """An import outcome: its summary sentence, then why items were skipped, failed or left."""
    summary = str(outcome.get("summary") or "").strip()
    if not summary:
        summary = ", ".join(
            f"{int(outcome.get(n) or 0)} {n.replace('_', ' ')}" for n in IMPORT_COUNTS
        )
    lines = [summary if summary.endswith(".") else f"{summary}."]
    finishing = int(outcome.get("finishing") or 0)
    partly = int(outcome.get("partly_applied") or 0)
    if finishing or partly:
        lines.append(
            f"  Of those in Knowledge: {finishing} being finished, "
            f"{partly} with changes that could not be applied."
        )
    for label, rows, code in (
        ("Skipped", outcome.get("skipped_reasons"), "reason_code"),
        ("Failed", outcome.get("failed_reasons"), "reason_code"),
        ("Left", outcome.get("left_reasons"), "reason"),
    ):
        counted = _reason_counts(rows, code)
        if counted:
            lines.append(f"  {label}: {counted}")
    followup = outcome.get("followup")
    if isinstance(followup, dict) and followup.get("status"):
        sent = followup.get("last_sent")
        attempts = int(followup.get("attempts") or 0)
        lines.append(
            f"  Follow-up {followup['status']}: sent the leftovers again {attempts} time(s)"
            + (f", {sent} item(s) last time" if sent is not None else "")
            + (f"; next look {followup['next_at']}" if followup.get("next_at") else "")
            + "."
        )
    return lines


def _import_status_lines(data: dict[str, Any], source_id: str) -> list[str]:
    """``import-status`` for a person: the state, how far it got, and how it ended."""
    if not data.get("supported"):
        why = data.get("unsupported_reason") or "this kind of source has no history to import"
        return [f"{source_id} does not take a history import: {why}."]
    status = str(data.get("status") or "idle")
    lines: list[str] = []
    if status == "idle":
        lines.append("No history import is waiting or running.")
    else:
        lines.append(
            f"History import {status}"
            + (
                " (a sync stopped partway; the next sync runs it again)"
                if data.get("retrying")
                else ""
            )
            + "."
        )
        progress = data.get("progress")
        if isinstance(progress, dict) and progress:
            lines.append(
                f"  {int(progress.get('tracked') or 0)} tracked, "
                f"{int(progress.get('brought_in') or 0)} read, "
                f"{int(progress.get('compiled') or 0)} compiled, "
                f"{int(progress.get('unprocessable') or 0)} set aside."
            )
        outcome = data.get("outcome")
        if isinstance(outcome, dict) and outcome:
            lines += _outcome_text(outcome)
    last = data.get("last_outcome")
    if isinstance(last, dict) and last:
        cancelled = " (cancelled)" if data.get("last_import_cancelled") else ""
        lines.append(f"Last import{cancelled}:")
        lines += [f"  {line}" for line in _outcome_text(last)]
    elif status == "idle" and not data.get("last_import"):
        lines.append("No import has run for this source.")
    return lines


@app.command("import-status")
def import_status(
    source_id: str = _source_arg,
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Whether a source takes a history import, whether one runs, and how the last one ended.

    \b
    An import's outcome counts its items in Knowledge, skipped, failed and left
    (each with why) and removed from the source, and says when it finished and
    how Beakr is sending what it left again. `beakr sources tracked <id>
    --state failed` lists the failed items.
    """
    project_id = _require_project(project)
    with get_sdk() as sdk:
        data = dict(sdk.request("GET", history_import_path(project_id, source_id)) or {})

    def render(answer: dict[str, Any]) -> None:
        for line in _import_status_lines(answer, source_id):
            console.print(line, highlight=False, markup=False, soft_wrap=True)

    def lines(answer: dict[str, Any]) -> list[str]:
        fields = [
            cell(source_id),
            f"status={answer.get('status') or 'idle'}",
            f"supported={'true' if answer.get('supported') else 'false'}",
        ]
        for prefix, key in (("", "outcome"), ("last_", "last_outcome")):
            outcome = answer.get(key)
            if isinstance(outcome, dict) and outcome:
                fields.append(f"{prefix}state={outcome.get('state') or ''}")
                fields += [
                    f"{prefix}{name}={int(outcome.get(name) or 0)}" for name in IMPORT_COUNTS
                ]
                fields += [
                    f"{prefix}{name}={int(outcome[name] or 0)}"
                    for name in IMPORT_PARTIAL_COUNTS
                    if name in outcome
                ]
        return ["\t".join(fields)]

    emit(data, json_out=json_out, render=render, lines=lines)


def _running_syncs(project_id: str, json_out: bool) -> None:
    """``sources status`` without a source: the sources whose sync is running now."""
    with get_sdk() as sdk:
        rows = [
            r for r in sdk.project_sources.list(project_id) if _sync_running(r.get("sync_status"))
        ]

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            console.print("No source is syncing.", markup=False)
            return
        print_table(
            ["ID", "Item", "Provider", "Sync"],
            [
                [
                    str(r.get("id") or ""),
                    short(r.get("external_item_name"), 32),
                    f"{r.get('provider') or ''} {r.get('external_item_type') or ''}".strip(),
                    str(r.get("sync_status") or ""),
                ]
                for r in data
            ],
            title=f"Syncing now ({len(data)})",
        )
        err_console.print("Stop one: beakr sources cancel-sync <id>", markup=False)

    emit(
        rows,
        json_out=json_out,
        render=render,
        lines=lambda data: _source_lines(data) if data else ["note: No source is syncing."],
        hint="beakr sources cancel-sync <id>" if rows else None,
    )


@app.command("cancel-sync")
def cancel_sync(
    source_id: str = _source_arg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Stop a source's running sync, Notion follow-up syncs included.

    \b
    Items already being read finish; the rest are skipped, and the next sync
    (nightly, or `beakr sources sync`) picks them up. Knowledge already built
    from the source stays. Compiles the sync already queued are not cancelled:
    `beakr kb compiles cancel --source <id>` skips them (and stops the sync too).
    The person who attached the source, a project admin or an organization
    admin may stop it.
    Exit codes: 0 stopping, 5 no sync is running, 4 no such source, 3 not allowed.
    """
    project_id = _require_project(project)
    confirm_or_require_yes(f"Stop the running sync of source {source_id}?", yes=yes)
    with get_sdk() as sdk:
        try:
            result = dict(cancels.cancel_sync(sdk, project_id, source_id) or {})
        except BeakrError as exc:
            if cancels.is_no_running_sync(exc):
                _add_fix(
                    exc,
                    f"Nothing to stop. `{source_command('status', source_id, project_id)}` shows "
                    "its last sync.",
                )
            raise

    def render(data: dict[str, Any]) -> None:
        for line in cancels.sync_cancel_lines(data, source_id):
            console.print(line, highlight=False, markup=False, soft_wrap=True)

    emit(
        result,
        json_out=json_out,
        render=render,
        lines=lambda data: [
            "\t".join(
                [
                    cell(source_id),
                    str(data.get("status") or "cancelling"),
                    f"job={cell(data.get('sync_job_id') or '-')}",
                ]
            )
        ],
        hint=source_command("status", source_id, project_id),
    )


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="sources")
