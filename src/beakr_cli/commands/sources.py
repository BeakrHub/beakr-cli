"""``beakr sources ls|attach|edit|rm|sync|status``: what a project pulls from its connections.

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
"""

from __future__ import annotations

import json
from typing import Any

import click
import typer
from beakr_sdk.errors import BeakrError, NotFoundError

from beakr_cli.compact import cell
from beakr_cli.errors import EXIT_NOT_FOUND, describe_error, error_object, exit_code_for
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk
from beakr_cli.source_links import SourceLinkError, parse_source_link, provider_slug

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


def _require_project(project: str | None) -> str:
    project_id = resolve_project(project)
    if not project_id:
        raise click.UsageError("Pass --project or run `beakr init` to bind this directory to one.")
    return str(project_id)


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


def _query_keys(sdk: Any) -> list[dict[str, Any]] | None:
    """The engine's ``source_query`` keys, or None from an engine that does not list them."""
    try:
        listing = sdk.project_sources.query_keys()
    except NotFoundError:
        return None
    return list((listing or {}).get("keys") or [])


def _check_query(
    keys: list[dict[str, Any]] | None,
    source_query: dict[str, Any] | None,
    *,
    provider: str | None = None,
    item_type: str | None = None,
) -> None:
    """Refuse a query key the engine does not list, or one the source's provider does not take.

    The engine decides (``GET /v1/integrations/source-query-keys``); this only
    answers before a request is sent, naming the keys that would fit. Without the
    listing (an older engine) the query is sent as given and the engine judges it.
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
    allowed, excluded = spec.get("item_types"), spec.get("excluded_item_types") or []
    if (allowed is not None and item_type not in allowed) or item_type in excluded:
        takes = f"only a {' or '.join(allowed)}" if allowed is not None else "no " + item_type
        raise click.UsageError(f"{key!r} applies to {takes}; this item is a {item_type}.")


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
        "\t".join(["sample", cell(item.get("id") or "-"), cell(item.get("name") or "")])
        for item in (data.get("sample") or [])[:10]
    ]
    lines += [
        "\t".join(
            [
                "group",
                cell(group.get("id") or "-"),
                cell(group.get("name") or ""),
                f"count={'>=' if group.get('count_capped') else ''}{int(group.get('count') or 0)}",
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
    for group in data.get("breakdown") or []:
        capped = "at least " if group.get("count_capped") else ""
        console.print(
            f"  {group.get('name') or group.get('id')}: {capped}{int(group.get('count') or 0)}",
            highlight=False,
            markup=False,
        )
    sample = (data.get("sample") or [])[:10]
    if sample:
        console.print("For example:", markup=False)
    for item in sample:
        title = item.get("name") or item.get("id")
        url = f"  {item['url']}" if item.get("url") else ""
        console.print(f"  {title}{url}", highlight=False, markup=False, soft_wrap=True)


def _emit_preview(data: Any, json_out: bool) -> None:
    """Print a rules preview; a query the provider rejected exits 1."""
    result = dict(data or {})
    emit(result, json_out=json_out, render=_render_preview, lines=_preview_lines)
    if result.get("provider_status") == "rejected":
        raise typer.Exit(1)


def _connection_label(row: dict[str, Any]) -> str:
    return f"{row.get('id')} ({row.get('label') or row.get('provider')})"


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
            ]
        )
        for r in rows
    ]


@app.command("ls")
def list_sources(project: str | None = _project_opt, json_out: bool = _json_opt) -> None:
    """List the project's sources: provider, item, Knowledge on or off, latest sync."""
    project_id = _require_project(project)
    with get_sdk() as sdk:
        rows = sdk.project_sources.list(project_id)

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            err_console.print(
                "No sources yet. Attach one with `beakr sources attach --url <link>`.",
                markup=False,
            )
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
                    str(r.get("sync_status") or "never"),
                ]
                for r in data
            ],
            title=f"Sources ({len(data)})",
        )

    emit(rows, json_out=json_out, render=render, lines=_source_lines)


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
        None, "--name", help="Display name (default: the title in the link, else the id)."
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
    mode: str | None = typer.Option(
        None, "--mode", help="agent (Beakr's source picker chooses; default) or all (every item)."
    ),
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
    view_hint: str | None = None

    if scope_mode == "all":
        if item or url:
            raise click.UsageError("--scope-mode all attaches everything: give no item or link.")
        if not provider:
            raise click.UsageError("--scope-mode all needs --provider.")
        slug = provider_slug(provider)
        item_id, kind = "__all__", item_type or "all"
        title = name or f"All {provider}"
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
        if not kind:
            raise click.UsageError(
                "The link does not say whether it is a file or a folder: pass --type file|folder."
            )
        title = name or link.name or item_id
        if link.view_id and source_query is None:
            view_hint = (
                f"The link opened one saved view of the database ({link.view_id}). To pull "
                f"only its rows: beakr sources edit <id> --query "
                f'\'{{"notion_view": "{link.view_id}"}}\''
            )
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

    project_id = _require_project(project)
    sync_error: BeakrError | None = None
    with get_sdk() as sdk:
        if source_query:
            _check_query(_query_keys(sdk), source_query, provider=slug, item_type=kind)
        connector_id = connector or pick_connector(sdk.integrations.list().get("data") or [], slug)
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
            err_console.print(f"Start it: beakr sources sync {source_id}", markup=False)
        if view_hint:
            # Unwrapped, so the command stays one copyable line.
            err_console.print(
                view_hint.replace("<id>", str(source_id)), markup=False, soft_wrap=True
            )

    emit(
        result,
        json_out=json_out,
        render=render,
        lines=lambda data: _source_lines([data]),
        hint=(
            f"beakr sources status {source_id}"
            if "sync" in result
            else f"beakr sources sync {source_id}"
        ),
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
    mode: str | None = typer.Option(None, "--mode", help="agent or all."),
    guidance: str | None = typer.Option(
        None, "--guidance", help="What to pull and what to record or skip, in words."
    ),
    clear_guidance: bool = typer.Option(False, "--clear-guidance", help="Drop the guidance."),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Change a source: Knowledge, nightly sync, query, mode, guidance. It does not sync.

    `beakr sources preview SRC --query ...` counts what a new query would keep first.
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
    with get_sdk() as sdk:
        if source_query:
            _check_attached_query(sdk, project_id, source_id, source_query)
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

    def render(data: dict[str, Any]) -> None:
        console.print(
            f"Updated {data.get('external_item_name')} ({source_id}): Knowledge "
            f"{'on' if data.get('ingest_wiki') else 'off'}, nightly sync "
            f"{'on' if data.get('auto_sync_wiki') else 'off'}.",
            highlight=False,
            markup=False,
        )

    emit(
        source,
        json_out=json_out,
        render=render,
        lines=lambda data: _source_lines([data]),
        hint=f"beakr sources sync {source_id}" if knowledge else None,
    )


def _check_attached_query(
    sdk: Any, project_id: str, source_id: str, source_query: dict[str, Any]
) -> None:
    """:func:`_check_query` against an attached source's provider and item type."""
    keys = _query_keys(sdk)
    if keys is None:
        return
    for row in sdk.project_sources.list(project_id):
        if str(row.get("id")) == source_id:
            _check_query(
                keys,
                source_query,
                provider=row.get("provider"),
                item_type=row.get("external_item_type"),
            )
            return
    # Not listed (another project's, or gone): the key alone, and the engine says the rest.
    _check_query(keys, source_query)


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
    mode: str | None = typer.Option(None, "--mode", help="agent or all."),
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
        if source_query:
            _check_attached_query(sdk, project_id, source_id, source_query)
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
        listing = _query_keys(sdk)
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
    """Detach a source from the project; what Knowledge already took from it stays."""
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
        hint=f"beakr sources status {source_id}",
    )


@app.command("status")
def status(
    source_id: str = _source_arg,
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """The latest sync of a source: its state, files done and failed, and why."""
    project_id = _require_project(project)
    with get_sdk() as sdk:
        result = sdk.project_sources.status(project_id, source_id)

    def render(data: dict[str, Any]) -> None:
        state = data.get("status")
        if not state:
            console.print(f"{source_id} has not synced yet.", markup=False)
            return
        console.print(
            f"Sync {state}: {data.get('files_completed', 0)}/{data.get('files_total', 0)} "
            f"files done, {data.get('files_failed', 0)} failed.",
            highlight=False,
            markup=False,
        )
        failed = [f for f in data.get("files") or [] if f.get("outcome") == "failed"]
        for entry in failed:
            console.print(
                f"  {entry.get('name')}  {entry.get('reason') or ''}",
                highlight=False,
                markup=False,
            )

    def lines(data: dict[str, Any]) -> list[str]:
        return [
            "\t".join(
                [
                    cell(source_id),
                    str(data.get("status") or "never"),
                    f"files_total={int(data.get('files_total') or 0)}",
                    f"files_completed={int(data.get('files_completed') or 0)}",
                    f"files_failed={int(data.get('files_failed') or 0)}",
                ]
            )
        ]

    emit(result, json_out=json_out, render=render, lines=lines)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="sources")
