"""Knowledge base commands."""

from __future__ import annotations

import json
import re
import sys
from itertools import islice
from pathlib import Path

import typer
from beakr_sdk.errors import BeakrError, NotFoundError
from beakr_sdk.resources.knowledge import result_text
from rich.markup import escape

from beakr_cli.citations import (
    SECTION_MARKER_RE,
    CitationValidationError,
    normalize_stances,
    validate_proposal_sections,
)
from beakr_cli.cited_answer import print_research_result, research_lines
from beakr_cli.compact import cell
from beakr_cli.errors import EXIT_ERROR, EXIT_USAGE
from beakr_cli.output import (
    console,
    err_console,
    is_interactive,
    machine_output,
    output_mode,
    page_rows,
    print_json,
    print_markdown,
    print_pages_table,
    print_search_results,
    print_table,
    readable_snippet,
)
from beakr_cli.project_binding import project_is_pinned, resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes, confirmed
from beakr_cli.proposal_accept import ReviewRequired, accept_reviewed, checked_hash
from beakr_cli.proposal_view import filed_by_line, payload_lines
from beakr_cli.research_metadata import public_research
from beakr_cli.scope import get_personal_project_id, scope_params
from beakr_cli.sdk import get_sdk
from beakr_cli.source_listing import document_lines, group_by_document

app = typer.Typer(help="Interact with your Beakr knowledge base.")
propose_app = typer.Typer(help="Create user-approved wiki write proposals.")
proposals_app = typer.Typer(help="Review and apply wiki write proposals.")
app.add_typer(propose_app, name="propose")
app.add_typer(proposals_app, name="proposals")

# Common scope options reused across commands. Every kb command spells it the same
# (-P, --scope, -s), as the card commands in kb_cards.py do.
_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    "-s",
    help="Project id, slug or name to scope the command.",
    callback=project_ref_callback,
)
_mine_opt = typer.Option(False, "--mine", "-m", help="Scope to your personal project only.")
_sections_opt = typer.Option(
    None,
    "--sections",
    help=(
        "Section metadata JSON array, or @path. Include event_start/event_end/date_precision "
        "and citations matching inline tokens like {{source_type:source_id}} in content."
    ),
)
_page_sections_opt = typer.Option(
    None,
    "--sections",
    help=(
        "The page: a JSON array (or @path) of {title, body, citations?, event_start?, "
        "event_end?, date_precision?}. Beakr writes the section markers and ids. Cite a "
        "claim in a body with a {{key}} token its section's citations declare ({key, "
        "source_type, source_title, stance: support|qualifies|contradicts, meta: {excerpt}}), "
        "or with the reference a read prints (memo.pdf › p. 7)."
    ),
)


def _scope_params(*, project: str | None = None, mine: bool = False) -> dict:
    """Build scope params and convert validation errors to CLI errors."""
    try:
        return scope_params(project=project, personal=mine)
    except ValueError as exc:
        err_console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc


def _project_id(*, project: str | None = None, mine: bool = False) -> str | None:
    """The project a read is scoped to (``--project``, ``--mine`` or the binding)."""
    return _scope_params(project=project, mine=mine).get("project_id")


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


@app.command()
def ls(
    page_type: str | None = typer.Option(None, "--type", "-t", help="Filter by page type."),
    all_scopes: bool = typer.Option(False, "--all", "-a", help="Show pages across all scopes."),
    roots_only: bool = typer.Option(False, "--roots", help="Show only root pages (no parent)."),
    parent: str | None = typer.Option(
        None,
        "--parent",
        "-p",
        help="Filter to children of this page (title or UUID).",
    ),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max pages to return."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """List knowledge base pages in the current scope."""
    parent_page_id = None
    if parent:
        parent_data = _resolve_page(parent, project=project, mine=mine)
        if parent_data:
            parent_page_id = parent_data["id"]

    scope = _scope_params(project=project, mine=mine)
    with get_sdk() as sdk:
        # Page through the list (at most 200 per request) up to --limit pages.
        pages = list(
            islice(
                sdk.knowledge.pages.iter(
                    project_id=scope.get("project_id"),
                    page_type=page_type or None,
                    roots_only=True if roots_only else None,
                    parent_page_id=parent_page_id,
                    page_size=min(limit, 200) if limit else 200,
                    all_scopes=True if all_scopes else None,
                ),
                limit,
            )
        )

    if not pages and not all_scopes and "project_id" not in scope:
        # With no project, the API lists only org-root pages, which is usually
        # none: an empty answer here used to look like an empty knowledge base.
        err_console.print(
            "No project is selected, so only organization-level pages are listed. "
            "Pass --project, bind this directory with `beakr init`, or use --all."
        )
    if not machine_output(pages, json_out=json_out):
        print_pages_table(pages)


# ---------------------------------------------------------------------------
# cat
# ---------------------------------------------------------------------------


@app.command()
def cat(
    page: str = typer.Argument(help="Page title or UUID."),
    rev: int | None = typer.Option(None, "--rev", "-r", help="Specific revision number."),
    section_id: str | None = typer.Option(
        None, "--section-id", help="Read only this section (the id a cut read names)."
    ),
    lines: str | None = typer.Option(
        None, "--lines", help="Read only these lines, start-end or one line (a grep hit's line)."
    ),
    full: bool = typer.Option(
        False, "--full", help="Every source record with its excerpt, not one per document."
    ),
    source_offset: int | None = typer.Option(
        None,
        "--source-offset",
        min=0,
        help="With --full: the first source record to return (a cut read names it).",
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Read a knowledge base page's content, with its citations as readable references.

    The text is the Knowledge door's `cat`, as the MCP tool shows it: each
    {{key}} citation is the name of what it cites, and the cited documents are
    listed after it. A read cut to the response budget names how to continue
    (section_id=, lines=, source_offset=); --section-id, --lines and
    --source-offset take those. --json prints the stored page record and its
    sources (the door's reply with --rev, --section-id, --lines or --full).
    """
    if source_offset is not None and not full:
        err_console.print("--source-offset pages the records of a --full read; add --full.")
        raise typer.Exit(EXIT_USAGE)
    page_data = _resolve_page(page, project=project, mine=mine)
    if not page_data:
        return

    door_only = rev is not None or section_id is not None or lines is not None or full
    if json_out and not door_only:
        with get_sdk() as sdk:
            source_list = sdk.knowledge.pages.sources(str(page_data["id"]))
        print_json({**page_data, "sources": source_list})
        return
    # The stored markdown carries raw {{artifact:...}} tokens; the door's `cat`
    # renders them as the references a reader can follow.
    _door_read(
        "cat",
        project=project,
        mine=mine,
        json_out=json_out,
        page=str(page_data["id"]),
        rev=rev,
        section_id=section_id,
        lines=lines,
        detail="full" if full else None,
        source_offset=source_offset,
    )


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _door_read(
    command: str, *, project: str | None, mine: bool, json_out: bool, **arguments: object
) -> None:
    """Run a Knowledge read command and print it as the MCP tools show it.

    The text is the engine's rendering, followed by one line per cited document
    (its citation tokens, filename, provider, the id ``read_file``/``beakr files
    download`` takes, one verbatim excerpt) -- the same text an agent gets from
    the ``knowledge_base`` tool. ``--json`` prints the door's reply unchanged.
    """
    from beakr_sdk.sources import public_source

    from beakr_cli.mcp_render import render

    project_id = _project_id(project=project, mine=mine)
    with get_sdk() as sdk:
        result = sdk.knowledge.command(command, scope=project_id, check=False, **arguments)
    outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else {}
    if json_out:
        print_json(result)
    else:
        sources = [public_source(r) for r in result.get("sources") or [] if isinstance(r, dict)]
        body = {"content": result_text(result), "sources": sources, "next": result.get("next")}
        sys.stdout.write(render("knowledge_base", body).rstrip() + "\n")
    if outcome.get("status") == "error":
        raise typer.Exit(1)


@app.command()
def sections(
    query: str = typer.Argument(help="What you are looking for, in plain words."),
    page_type: str | None = typer.Option(
        None, "--type", "-t", help="Only sections of this page type (e.g. person, decision)."
    ),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max sections (default 10)."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Find the sections that answer a question, with the passages they cite. START HERE.

    Semantic search over sections; each hit carries the verbatim document
    passage it rests on, so you can quote it without opening the file. Each cited
    document is listed with where it lives and where each passage sits
    (path › p. 7). The same command as the MCP tool's `knowledge_base sections`.
    """
    _door_read(
        "sections",
        project=project,
        mine=mine,
        json_out=json_out,
        query=query,
        page_type=page_type,
        limit=limit,
    )


@app.command()
def search(
    query: str = typer.Argument(help="Search query."),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Find pages by meaning and keywords, with the part of each page that matched.

    For the exact section and its cited passage use `beakr kb sections`.
    """
    project_id = _project_id(project=project, mine=mine)
    with get_sdk() as sdk:
        data = sdk.knowledge.pages.search(query, project_id=project_id, limit=limit)
    results = page_rows(data)

    if not machine_output(results, json_out=json_out, lines=_search_lines):
        print_search_results(results)


def _search_lines(results: list[dict]) -> list[str]:
    """``id<TAB>title<TAB>type=..<TAB>the matching passage`` per page."""
    lines = []
    for hit in results:
        snippet = readable_snippet(str(hit.get("content_snippet") or hit.get("summary") or ""))
        columns = [str(hit.get("id") or ""), str(hit.get("title") or "")]
        if hit.get("page_type"):
            columns.append(f"type={hit['page_type']}")
        if snippet:
            columns.append(snippet[:300])
        lines.append("\t".join(columns))
    return lines


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


@app.command()
def grep(
    pattern: str = typer.Argument(help="Literal text (or a regex with --regex)."),
    regex: bool = typer.Option(False, "--regex", "-E", help="Treat the pattern as a regex."),
    context: int | None = typer.Option(
        None, "--context", "-C", help="Lines of context around each match (max 10)."
    ),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max pages."),
    after_title: str | None = typer.Option(
        None, "--after-title", help="Continue a listing after this page title (as a reply names)."
    ),
    after_page_id: str | None = typer.Option(
        None, "--after-page-id", help="With --after-title: the page id a reply names beside it."
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Find the lines that contain a word, id or exact name, with the section they are in.

    A long listing ends "continue with after_title=... after_page_id=...":
    pass both back as --after-title and --after-page-id for the next pages.
    """
    _door_read(
        "grep",
        project=project,
        mine=mine,
        json_out=json_out,
        query=pattern,
        mode="regex" if regex else None,
        context_lines=context,
        limit=limit,
        after_title=after_title,
        after_page_id=after_page_id,
    )


# ---------------------------------------------------------------------------
# blame
# ---------------------------------------------------------------------------


@app.command()
def blame(
    page: str = typer.Argument(help="Page title or UUID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show paragraph-level source attribution."""
    page_data = _resolve_page(page, project=project, mine=mine)
    if not page_data:
        return

    page_id = page_data["id"]
    with get_sdk() as sdk:
        data = sdk.knowledge.pages.blame(page_id)
    blame_entries = data.get("blame", [])

    if machine_output(blame_entries, json_out=json_out):
        return

    for entry in blame_entries:
        source = entry.get("source_title", entry.get("compiled_by", "unknown"))
        rev = entry.get("revision", "?")
        text = entry.get("text", "").strip()
        if not text:
            continue
        display = text[:120] + "..." if len(text) > 120 else text
        console.print(
            f"[dim]rev {escape(str(rev))}[/dim]  [cyan]{escape(str(source[:40]))}[/cyan]  "
            f"{escape(str(display))}"
        )


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@app.command()
def log(
    page: str | None = typer.Argument(None, help="Page title or UUID. Omit for global."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show revision history for a page, or recent revisions across the knowledge base."""
    if page:
        page_data = _resolve_page(page, project=project, mine=mine)
        if not page_data:
            return
        page_id = page_data["id"]
        with get_sdk() as sdk:
            data = sdk.knowledge.pages.revisions(page_id)
        revisions = page_rows(data)

        if machine_output(revisions, json_out=json_out, lines=_revision_lines):
            return

        columns = ["Rev", "Compiled By", "Summary", "Date"]
        rows = []
        for r in revisions:
            rows.append(
                [
                    str(r.get("revision", "")),
                    r.get("compiled_by", "")[:30],
                    (r.get("summary", "") or "")[:50],
                    r.get("created_at", "")[:16] if r.get("created_at") else "",
                ]
            )
        print_table(columns, rows, title=page_data.get("title", ""))
    else:
        # The Knowledge door's `log` with no page: recent revisions in scope,
        # printed as its text (piped too), not the reply's envelope.
        _door_read("log", project=project, mine=mine, json_out=json_out)


def _revision_lines(revisions: list[dict]) -> list[str]:
    """``rev=N<TAB>date<TAB>by=..<TAB>summary`` per revision, newest first as listed."""
    lines = []
    for r in revisions:
        columns = [f"rev={r.get('revision', '')}", str(r.get("created_at") or "")[:16]]
        by = r.get("editor_name") or r.get("compiled_by")
        if by:
            columns.append(f"by={cell(by)}")
        if r.get("summary"):
            columns.append(cell(r["summary"], width=200))
        lines.append("\t".join(columns))
    return lines


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


@app.command()
def sources(
    page: str = typer.Argument(help="Page title or UUID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show the documents a page cites and where each one lives.

    One line per document: filename, provider and a link or path to the
    original, or for a file only Beakr holds its file id and the
    `beakr files download` command. --json prints every citation as a public
    source record (the shape the MCP server and the SDKs return).
    """
    page_data = _resolve_page(page, project=project, mine=mine)
    if not page_data:
        return

    page_id = page_data["id"]
    with get_sdk() as sdk:
        # Every citation on the page, as public source records.
        source_list = sdk.knowledge.pages.sources(page_id)

    if machine_output(
        source_list,
        json_out=json_out,
        lines=document_lines,
        hint=f"beakr kb sources {page_id} --json  (every citation, with its source_ref)"
        if source_list
        else None,
    ):
        return

    if not source_list:
        err_console.print("No sources.")
        return
    _print_documents(source_list)


def _print_documents(source_list: list[dict]) -> None:
    """The person's view of a page's sources: one numbered line per document."""
    rows = []
    for index, group in enumerate(group_by_document(source_list), 1):
        rows.append(
            [
                str(index),
                group["where"],
                str(group["citations"]),
                ", ".join(group["stances"]),
            ]
        )
    print_table(["#", "Source", "Citations", "Stance"], rows)


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


@app.command()
def provenance(
    page: str = typer.Argument(help="Page title or UUID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show section provenance rollups with inline citations and stance."""
    page_data = _resolve_page(page, project=project, mine=mine)
    if not page_data:
        return

    page_id = page_data["id"]
    with get_sdk() as sdk:
        data = sdk.knowledge.pages.section_provenance(page_id)
    prov = data.get("provenance", {})

    if machine_output(prov, json_out=json_out):
        return

    if not prov:
        err_console.print("No section provenance data.")
        return

    for section_id, section_data in prov.items():
        if isinstance(section_data, dict):
            title = section_data.get("title") or section_id
            citations = section_data.get("sources", [])
        else:
            title = section_id
            citations = section_data if isinstance(section_data, list) else [section_data]

        console.print(f"\n[bold]{escape(str(title))}[/bold]")
        for cit in citations:
            stance = cit.get("stance", "support")
            source = cit.get("source_title", cit.get("source_id", "unknown"))
            style = {"support": "green", "contradicts": "red", "qualifies": "yellow"}.get(
                stance, "white"
            )
            inline = " inline" if cit.get("inline") else ""
            key = f" ({cit.get('key')})" if cit.get("key") else ""
            console.print(
                f"  [{style}]{escape(str(stance))}{inline}[/{style}]  "
                f"{escape(str(source))}{escape(key)}"
            )


# ---------------------------------------------------------------------------
# links (backlinks)
# ---------------------------------------------------------------------------


@app.command()
def links(
    page: str = typer.Argument(help="Page title or UUID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show pages that link to this page."""
    page_data = _resolve_page(page, project=project, mine=mine)
    if not page_data:
        return

    page_id = page_data["id"]
    with get_sdk() as sdk:
        # Every row of the paginated WikiBacklinks list, as a list.
        backlinks = sdk.knowledge.pages.backlinks(page_id)

    if machine_output(backlinks, json_out=json_out):
        return

    if not backlinks:
        err_console.print("No backlinks.")
        return

    columns = ["Title"]
    rows = [[b.get("title", "")] for b in backlinks]
    print_table(columns, rows)


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------


@app.command()
def timeline(
    query: str | None = typer.Argument(
        None,
        help="Keyword filter (matches section titles, page titles, content).",
    ),
    start_date: str | None = typer.Option(None, "--start", help="Start date YYYY-MM-DD."),
    end_date: str | None = typer.Option(None, "--end", help="End date YYYY-MM-DD."),
    page_type: str | None = typer.Option(None, "--type", "-t", help="Filter by page type."),
    include_content: bool = typer.Option(
        False,
        "--content",
        "-c",
        help="Include section markdown content.",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max events (max 100)."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Query temporal events across the knowledge base by date range and keyword."""
    project_id = _project_id(project=project, mine=mine)
    with get_sdk() as sdk:
        data = sdk.knowledge.pages.timeline(
            project_id=project_id,
            q=query or None,
            start_date=start_date or None,
            end_date=end_date or None,
            page_type=page_type or None,
            include_content=True if include_content else None,
            limit=min(limit, 100),
        )
    events = page_rows(data)

    if machine_output(events, json_out=json_out):
        return

    if not events:
        err_console.print("No events found.")
        return

    for e in events:
        date_str = e.get("event_start", "?")
        if e.get("event_end") and e["event_end"] != date_str:
            date_str += f" -- {e['event_end']}"
        precision = e.get("date_precision", "")
        if precision and precision != "day":
            date_str += f" ({precision})"
        console.print(
            f"[dim]{escape(str(date_str))}[/dim]  "
            f"[cyan]\\[{escape(str(e.get('page_type', '')))}][/cyan]  "
            f"[bold]{escape(str(e.get('title', '')))}[/bold]"
        )
        console.print(f"  Page: {e.get('page_title', '')}", markup=False)
        if e.get("content"):
            console.print()
            print_markdown(e["content"])
            console.print()


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


@app.command()
def graph(
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Dump the knowledge graph (nodes + edges)."""
    project_id = _project_id(project=project, mine=mine)
    with get_sdk() as sdk:
        data = sdk.knowledge.pages.graph(project_id=project_id)

    if machine_output(data, json_out=json_out):
        return

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    console.print(f"{len(nodes)} nodes, {len(edges)} edges")
    for n in nodes[:20]:
        # WikiGraphNode: ``label`` is the page title, ``page_type`` its kind
        # (``type`` is the node kind, "page" for every page).
        kind = n.get("page_type") or n.get("type") or ""
        line = f"  [{kind}] {n.get('label') or n.get('id', '')}"
        # Not markup: "[project]" would otherwise be read as a style tag and dropped.
        console.print(line, markup=False, highlight=False)
    if len(nodes) > 20:
        console.print(f"  ... and {len(nodes) - 20} more", markup=False)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@app.command()
def stats(
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show knowledge base statistics for the current scope."""
    project_id = _project_id(project=project, mine=mine)
    with get_sdk() as sdk:
        data = sdk.knowledge.pages.stats(project_id=project_id)
    if not machine_output(data, json_out=json_out):
        console.print(f"Pages:   {data.get('pages', 0)}", markup=False)
        console.print(f"Sources: {data.get('sources', 0)}", markup=False)


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


@app.command()
def research(
    query: str = typer.Argument(help="Question to research."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    quick: bool = typer.Option(
        False,
        "--quick",
        help="A short cited answer within 15 seconds from one search (no connected services).",
    ),
) -> None:
    """Ask a question and get a researched answer with citations."""
    project_id = _project_id(project=project, mine=mine)
    with get_sdk() as sdk:
        data = public_research(sdk.knowledge.research(query, project_id=project_id, quick=quick))

    if machine_output(data, json_out=json_out, lines=research_lines):
        return

    print_research_result(data)


# ---------------------------------------------------------------------------
# proposal helpers
# ---------------------------------------------------------------------------


def _proposal_scope(project: str | None, *, mine: bool = False) -> dict:
    """Build required scope for write proposals.

    A write needs a project someone chose: ``--project``, ``--mine``, or a
    pin (``BEAKR_PROJECT_ID`` or this directory's binding). The CLI's default
    project is a read fallback, not a choice, so it never decides where a
    write lands.
    """
    selected = sum(bool(v) for v in (project, mine))
    if selected == 0 and project_is_pinned():
        pinned = resolve_project(None)
        if pinned:
            return {"project_id": pinned}
    if selected != 1:
        err_console.print(
            "Provide exactly one of --project or --mine for write proposals "
            "(or run it in a directory bound with `beakr init`)."
        )
        raise typer.Exit(1)
    if project:
        return {"project_id": project}
    personal_project_id = get_personal_project_id()
    if not personal_project_id:
        err_console.print("Could not resolve your personal project.")
        raise typer.Exit(1)
    return {"project_id": personal_project_id}


def _proposal_filter_scope(project: str | None, *, mine: bool = False) -> dict:
    """Build optional scope filter for listing proposals."""
    selected = sum(bool(v) for v in (project, mine))
    if selected > 1:
        err_console.print("Provide at most one of --project or --mine.")
        raise typer.Exit(1)
    if selected == 0:
        return {}
    return _proposal_scope(project, mine=mine)


def _read_content_arg(content: str | None, file: Path | None) -> str:
    """Read content from an inline argument or a file."""
    if bool(content) == bool(file):
        err_console.print("Provide exactly one of --content or --file.")
        raise typer.Exit(1)
    if file:
        return file.read_text()
    return content or ""


def _load_json_arg(value: str) -> object:
    """Load JSON from an inline value or @path."""
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text())
    return json.loads(value)


def _load_sections_arg(value: str | None) -> list[dict]:
    """Load proposal section metadata from JSON."""
    if not value:
        return []
    data = _load_json_arg(value)
    if not isinstance(data, list):
        err_console.print("--sections must be a JSON array.")
        raise typer.Exit(1)
    return data


def _validate_sections_arg(
    content: str | None,
    sections: list[dict],
    *,
    require_content: bool,
) -> None:
    try:
        validate_proposal_sections(content, sections, require_content=require_content)
    except CitationValidationError as exc:
        err_console.print(f"Invalid --sections:\n{exc}", markup=False)
        raise typer.Exit(1) from exc


def _create_proposal(body: dict) -> dict:
    """Stage one of the CLI's proposal shapes (``create``, ``edit``, ``mv`` ...)."""
    fields = dict(body)
    action = fields.pop("action")
    # Every caller builds `body` with _proposal_scope, so project_id is always the
    # project's id; the SDK takes it as a required keyword since beakr-sdk c0765e9.
    project_id = fields.pop("project_id")
    with get_sdk() as sdk:
        return sdk.knowledge.proposals.create(action, project_id=project_id, **fields)


def _print_proposal(proposal: dict, *, with_payload: bool = False) -> None:
    """Print a proposal's summary, its payload when asked, and its content hash last.

    The hash comes last so it is on screen when the reviewer copies it into
    `kb proposals accept --reviewed-hash`; above a long payload it scrolled away.
    """
    console.print(f"[bold]Proposal:[/bold] {escape(str(proposal.get('id')))}")
    console.print(f"Type:   {proposal.get('proposal_type')}", markup=False)
    console.print(f"Status: {proposal.get('status')}", markup=False)
    if proposal.get("page_title"):
        console.print(f"Page:   {proposal.get('page_title')}", markup=False)
    if proposal.get("summary"):
        console.print(f"Summary: {proposal.get('summary')}", markup=False)
    if filed_by_line(proposal):
        console.print(filed_by_line(proposal), markup=False)
    payload = proposal.get("payload") or {}
    if proposal.get("proposal_type") == "find_replace":
        console.print(
            f"Changes: {payload.get('total_matches', 0)} match(es) across "
            f"{payload.get('total_pages', 0)} page(s)",
            markup=False,
        )
    elif proposal.get("proposal_type") == "archive":
        console.print(f"Pages:  {len(payload.get('archive_items') or [])}")
    elif proposal.get("proposal_type") == "mv":
        console.print(f"Moves:  {len(payload.get('reorg') or [])}")
    if with_payload and isinstance(payload, dict):
        console.print()
        for text, style in payload_lines(payload):
            console.print(text, style=style, markup=False, highlight=False)
        console.print()
    if proposal.get("changes_card_tables"):
        console.print("Changes card table rows: accept with --reviewed-hash and this content hash.")
    if proposal.get("content_hash"):
        # Printed plainly: `kb proposals accept --reviewed-hash` takes it back.
        console.print(f"Content hash: {proposal['content_hash']}", markup=False)


def _accept_created_proposal(
    proposal: dict, *, accept: bool, yes: bool, auto_approve: bool = False
) -> None:
    _print_proposal(proposal)
    if not accept:
        return
    # The proposal exists either way: a "no" leaves it pending, not failed.
    # Auto-approve is the person's own standing yes, so it is not asked again.
    if not auto_approve and not confirmed("Accept and apply this proposal?", yes):
        return
    # Bound to the proposal this command created and just showed.
    result = _accept_created(proposal)
    console.print(f"Accepted proposal {result.get('proposal_id', proposal['id'])}.", markup=False)


def _accept_created(proposal: dict) -> dict:
    """Accept the proposal this command staged; one auto-approve already applied is done.

    A write through the Knowledge door is applied by the owner's auto-approve
    worker within seconds, so ``--accept`` races it: the worker winning left the
    accept refused ("Proposal is accepted, not pending") and the command exiting
    1 though the page existed. An accepted proposal is the outcome asked for.
    """
    proposal_id = str(proposal["id"])
    applied = {"status": "accepted", "proposal_id": proposal_id, "applied_by": "auto-approve"}
    if proposal.get("status") == "accepted":
        return applied
    try:
        return _accept_bound(proposal_id, proposal.get("content_hash"))
    except BeakrError:
        with get_sdk() as sdk:
            current = sdk.knowledge.proposals.show(proposal_id)
        if isinstance(current, dict) and current.get("status") == "accepted":
            return applied
        raise


def _accept_bound(proposal_id: str, reviewed_hash: str | None) -> dict:
    """Accept bound to ``reviewed_hash``; a card-table proposal without one is refused (exit 2)."""
    try:
        with get_sdk() as sdk:
            return accept_reviewed(sdk, proposal_id, reviewed_hash)
    except ReviewRequired as refused:
        err_console.print(
            f"{refused} Review it with `beakr kb proposals show {proposal_id}`, then run "
            f"`beakr kb proposals accept {proposal_id} --reviewed-hash <its content hash>`.",
            markup=False,
        )
        raise typer.Exit(EXIT_USAGE) from refused


def _finish_proposal(
    proposal: dict, *, accept: bool, yes: bool, json_out: bool, auto_approve: bool = False
) -> None:
    """Show (or print as JSON) a created proposal and accept it when asked.

    ``--accept --yes`` accepts in every output mode. JSON and piped output used
    to skip the accept step entirely, so a CI job asking for ``--accept --yes``
    left the proposal pending and still exited 0. Without ``--yes`` there is no
    one to confirm in those modes, so ``--accept`` alone is refused rather than
    ignored. ``auto_approve`` (the write's reply said the owner's auto-approve
    applies it) needs no confirmation and no accept hint.
    """
    if output_mode(json_out) == "human":
        _accept_created_proposal(proposal, accept=accept, yes=yes, auto_approve=auto_approve)
        return
    accept_hint = (
        f"auto-approve applies it; confirm with beakr kb proposals show {proposal['id']}"
        if auto_approve
        else f"beakr kb proposals accept {proposal['id']}"
    )
    if accept and not yes and not auto_approve:
        machine_output(proposal, json_out=json_out, hint=accept_hint)
        err_console.print(
            "Not accepted: --accept needs --yes when output is JSON or piped. "
            f"Accept with `{accept_hint}`.",
            markup=False,
        )
        raise typer.Exit(1)
    if accept:
        # The proposal this command just created: what the caller itself submitted.
        result = _accept_created(proposal)
        # The proposal as it stands now: the create's own "pending" beside
        # accepted=true read as though the accept had not taken.
        status = (result or {}).get("status") or "accepted"
        machine_output(
            {**proposal, "status": status, "accepted": True, "accept_result": result},
            json_out=json_out,
        )
        return
    machine_output(proposal, json_out=json_out, hint=accept_hint)


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


@propose_app.command("create")
def propose_create(
    title: str = typer.Option(..., "--title", "-t", help="New page title."),
    sections: str | None = _page_sections_opt,
    content: str | None = typer.Option(
        None,
        "--content",
        "-c",
        help="The page as one markdown text, instead of --sections.",
    ),
    file: Path | None = typer.Option(None, "--file", "-f", help="Read --content from a file."),
    page_type: str | None = typer.Option(
        None, "--type", help="Page type (default: Beakr's choice; topic for --content)."
    ),
    summary: str = typer.Option("", "--summary", help="What the page is about, in one line."),
    rationale: str | None = typer.Option(
        None, "--rationale", help="Why this page should exist (default: the summary)."
    ),
    parent: str | None = typer.Option(None, "--parent", help="Parent page title or UUID."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    accept: bool = typer.Option(False, "--accept", help="Prompt to accept after creating."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation with --accept."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Propose creating a wiki page.

    Give the page as --sections, a list of {title, body} objects, and Beakr
    writes the section markers and ids (the knowledge_base_write `new` action).
    --content alone stages one markdown text as it is; with --sections that
    carry no body, --content is the body of their one section.
    """
    section_data = normalize_stances(_load_sections_arg(sections))
    scope = _proposal_scope(project, mine=mine)
    page_content = _read_content_arg(content, file) if (content or file) else None
    if page_content is not None and section_data and not _marked_sections(section_data):
        section_data = _content_as_section(page_content, section_data)
        page_content = None
    if page_content is None:
        if not section_data:
            err_console.print(
                "Give the page as --sections [{title, body, citations?}], or --content/--file.",
                markup=False,
            )
            raise typer.Exit(EXIT_USAGE)
        proposal, auto_approve = _stage_new_page(
            title=title,
            sections=section_data,
            page_type=page_type,
            summary=summary,
            rationale=rationale or summary or f"Create the page {title}.",
            # Resolved first: the door puts a page whose parent it cannot find
            # at the root, where the older route answered 404 for a typo.
            parent=str(_resolve_page(parent, project=scope["project_id"])["id"])
            if parent
            else None,
            scope=scope["project_id"],
            json_out=json_out,
        )
        if proposal is not None:
            _finish_proposal(
                proposal,
                accept=accept,
                yes=yes,
                json_out=json_out,
                auto_approve=auto_approve,
            )
        return
    # The older shape: markdown whose <!-- sec:ID --> markers --sections annotates.
    _validate_sections_arg(page_content, section_data, require_content=True)
    body = {
        "action": "create",
        "title": title,
        "content": page_content,
        "page_type": page_type or "topic",
        "summary": summary,
        "sections": section_data,
        "parent": parent,
        "rationale": rationale,
        **scope,
    }
    proposal = _create_proposal(body)
    _finish_proposal(proposal, accept=accept, yes=yes, json_out=json_out)


def _marked_sections(sections: list[dict]) -> bool:
    """Whether ``sections`` annotate marked content (each names its marker's id)."""
    return any(isinstance(section, dict) and section.get("id") for section in sections)


def _content_as_section(content: str, sections: list[dict]) -> list[dict]:
    """``--content`` as the body of the one ``--sections`` entry that has none.

    So ``--content "... {{conversation:x}}" --sections '[{"title": ..., "citations":
    [...]}]'`` stages a one-section page without the caller writing a marker.
    Content that already has markers, or several sections, is refused here
    with one message rather than staged as a guess.
    """
    if SECTION_MARKER_RE.search(content):
        _usage_error(
            "--content has <!-- sec:ID --> markers, so each --sections entry needs its id. "
            "Or give the page as --sections [{title, body}] alone and Beakr writes the markers."
        )
    if len(sections) != 1 or not isinstance(sections[0], dict) or sections[0].get("body"):
        _usage_error(
            "With --content, give one --sections entry without a body (its title and "
            "citations), or give the whole page as --sections [{title, body}] alone."
        )
    return [{**sections[0], "body": content}]


def _usage_error(message: str) -> None:
    err_console.print(message, markup=False)
    raise typer.Exit(EXIT_USAGE)


#: The phrase the door's reply opens its auto-approve note with: the owner's
#: setting applies the proposal, so no one should be told to accept it. Read
#: only from a server whose outcome does not say so in ``auto_approve``.
_AUTO_APPROVE_REPLY = "Auto-approve is on"


def _auto_approved(result: object, reply: str) -> bool:
    """Whether the owner's auto-approve applies what this write staged.

    ``outcome.auto_approve`` says so where the server sends it (true or false);
    an older server says it only in the reply's text.
    """
    outcome = result.get("outcome") if isinstance(result, dict) else None
    flag = outcome.get("auto_approve") if isinstance(outcome, dict) else None
    if isinstance(flag, bool):
        return flag
    return _AUTO_APPROVE_REPLY in reply


def _stage_new_page(
    *,
    title: str,
    sections: list[dict],
    page_type: str | None,
    summary: str,
    rationale: str,
    parent: str | None,
    scope: str,
    json_out: bool,
) -> tuple[dict | None, bool]:
    """Stage a new page from its sections: the proposal as ``show`` reads it, and
    whether the owner's auto-approve applies it.

    Through the Knowledge write door (``new``), where Beakr writes the section
    markers and ids, lays a new card out as a card and checks the citations:
    a refusal is the engine's, reported once by the root error handler.

    The door's reply text is printed (on stderr when output is piped or JSON,
    so stdout stays the proposal): it says when the owner's auto-approve
    applies the proposal, and names what a reviewer should know -- citations
    still owed, a page with a matching title, the page type used in place of
    the one asked for. An auto-approved write still answers its proposal's id,
    which is read back so ``--accept`` and the content hash work as for any
    proposal. A reply with no proposal at all is printed as the door answered.
    """
    with get_sdk() as sdk:
        result = sdk.knowledge.write(
            "new",
            title=title,
            sections=sections,
            page_type=page_type,
            summary=summary or None,
            parent=parent,
            rationale=rationale,
            scope=scope,
        )
        reply = result_text(result) if isinstance(result, dict) else str(result or "")
        staged = result.get("proposal") if isinstance(result, dict) else None
        proposal = (
            sdk.knowledge.proposals.show(str(staged["id"]))
            if isinstance(staged, dict) and staged.get("id")
            else None
        )
    if proposal is None:
        if not machine_output(result, json_out=json_out, lines=lambda r: reply.splitlines()):
            console.print(reply, markup=False)
        return None, False
    if reply:
        (console if output_mode(json_out) == "human" else err_console).print(
            reply, markup=False, highlight=False
        )
    return proposal, _auto_approved(result, reply)


@propose_app.command("edit")
def propose_edit(
    page: str = typer.Argument(..., help="Page title or UUID."),
    content: str | None = typer.Option(None, "--content", "-c", help="Replacement markdown."),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Read replacement markdown from file.",
    ),
    patches: str | None = typer.Option(
        None,
        "--patches",
        help="Patch JSON array, or @path to a JSON file.",
    ),
    title: str | None = typer.Option(None, "--title", help="Optional new title."),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    sections: str | None = _sections_opt,
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    accept: bool = typer.Option(False, "--accept", help="Prompt to accept after creating."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation with --accept."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Propose editing a wiki page."""
    if patches and (content or file):
        err_console.print("Use either --patches or --content/--file, not both.")
        raise typer.Exit(1)
    section_data = normalize_stances(_load_sections_arg(sections))
    body = {
        "action": "edit",
        "page": page,
        "title": title,
        "summary": summary,
        "sections": section_data,
        **_proposal_scope(project, mine=mine),
    }
    if patches:
        _validate_sections_arg(None, section_data, require_content=False)
        body["patches"] = _load_json_arg(patches)
    else:
        page_content = _read_content_arg(content, file)
        _validate_sections_arg(page_content, section_data, require_content=True)
        body["content"] = page_content
    proposal = _create_proposal(body)
    _finish_proposal(proposal, accept=accept, yes=yes, json_out=json_out)


@propose_app.command("replace")
def propose_replace(
    find: str = typer.Option(..., "--find", help="Text or regex to find."),
    replace: str = typer.Option("", "--replace", help="Replacement text."),
    regex: bool = typer.Option(False, "--regex", help="Treat --find as regex."),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    accept: bool = typer.Option(False, "--accept", help="Prompt to accept after creating."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation with --accept."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Propose find/replace across pages in a scope."""
    body = {
        "action": "find_replace",
        "replacements": [{"find": find, "replace": replace, "regex": regex}],
        "summary": summary,
        **_proposal_scope(project, mine=mine),
    }
    proposal = _create_proposal(body)
    _finish_proposal(proposal, accept=accept, yes=yes, json_out=json_out)


@propose_app.command("move")
def propose_move(
    page: str = typer.Argument(..., help="Page title or UUID."),
    title: str | None = typer.Option(None, "--title", help="Optional new title."),
    parent: str | None = typer.Option(None, "--parent", help="Optional new parent."),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    accept: bool = typer.Option(False, "--accept", help="Prompt to accept after creating."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation with --accept."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Propose moving or renaming a wiki page."""
    body = {
        "action": "mv",
        "page": page,
        "title": title,
        "parent": parent,
        "summary": summary,
        **_proposal_scope(project, mine=mine),
    }
    proposal = _create_proposal(body)
    _finish_proposal(proposal, accept=accept, yes=yes, json_out=json_out)


@propose_app.command("archive")
def propose_archive(
    page: str = typer.Argument(..., help="Page title or UUID."),
    include_children: bool = typer.Option(True, "--children/--no-children"),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    accept: bool = typer.Option(False, "--accept", help="Prompt to accept after creating."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation with --accept."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Propose archiving a wiki page."""
    body = {
        "action": "archive",
        "page": page,
        "include_children": include_children,
        "summary": summary,
        **_proposal_scope(project, mine=mine),
    }
    proposal = _create_proposal(body)
    _finish_proposal(proposal, accept=accept, yes=yes, json_out=json_out)


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------


# `ls` is the spec's listing verb (W7 item 2: `beakr <resource> ls|show|create|edit|rm`).
# `list` stays registered, hidden, so scripts written against the earlier name keep
# working; tests/test_cli_command_grammar.py fails if a visible `list` returns.
@proposals_app.command("ls")
@proposals_app.command("list", hidden=True, deprecated=True)
def proposals_list(
    status_filter: str = typer.Option(
        "pending",
        "--status",
        help="pending, accepted, dismissed, superseded or resolved.",
    ),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=200, help="Proposals a page."),
    cursor: str | None = typer.Option(
        None, "--cursor", help="The next page: the cursor a previous listing printed."
    ),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """List wiki proposals, one page at a time (--cursor continues)."""
    params: dict = {
        "status": status_filter,
        "limit": limit,
        "cursor": cursor,
        **_proposal_filter_scope(project, mine=mine),
    }
    with get_sdk() as sdk:
        # The list route pages with a cursor; proposals.list returns one page's
        # rows only, so the envelope is read here to say where the next page is.
        page = sdk.request(
            "GET",
            "/v1/knowledge/wiki/proposals",
            params={key: value for key, value in params.items() if value is not None},
        )
    proposals = page_rows(page)
    has_more = isinstance(page, dict) and page.get("has_more")
    next_cursor = page.get("next_cursor") if has_more else None
    more = f"beakr kb proposals ls --cursor {next_cursor}" if next_cursor else None
    if machine_output(proposals, json_out=json_out, lines=_proposal_lines, hint=more):
        if more and output_mode(json_out) == "json":
            err_console.print(
                f"More proposals: {more}", highlight=False, soft_wrap=True, markup=False
            )
        return
    rows = [
        [
            p.get("id", "")[:8],
            p.get("proposal_type", ""),
            p.get("status", ""),
            p.get("page_title", "")[:32],
            p.get("summary", "")[:48],
            _filer(p)[:32],
        ]
        for p in proposals
    ]
    print_table(
        ["ID", "Type", "Status", "Page", "Summary", "Filed by"], rows, title="Wiki Proposals"
    )
    if more:
        err_console.print(f"More proposals: {more}", highlight=False, soft_wrap=True, markup=False)


def _filer(proposal: dict) -> str:
    """Who filed it, without the "filed by" lead: ``Dana Reyes via claude-code/2.1``."""
    filed_by = proposal.get("filed_by")
    text = filed_by.get("text") if isinstance(filed_by, dict) else None
    if not isinstance(text, str):
        return ""
    return text.strip().removeprefix("filed by ")


def _proposal_lines(proposals: list[dict]) -> list[str]:
    """``id<TAB>type=..<TAB>status=..<TAB>page=..<TAB>filed_by=..<TAB>summary`` per proposal."""
    lines = []
    for p in proposals:
        columns = [str(p.get("id") or "")]
        for key, name in (("proposal_type", "type"), ("status", "status"), ("page_title", "page")):
            if p.get(key):
                columns.append(f"{name}={cell(p[key])}")
        if _filer(p):
            columns.append(f"filed_by={cell(_filer(p))}")
        if p.get("summary"):
            columns.append(cell(p["summary"], width=200))
        lines.append("\t".join(columns))
    return lines


@proposals_app.command("show")
def proposals_show(
    proposal_id: str = typer.Argument(..., help="Proposal ID."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        "-s",
        help="Only if the proposal is in this project (id, slug or name).",
        callback=project_ref_callback,
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Show one wiki proposal: its change as a diff, and the content hash to accept it with."""
    with get_sdk() as sdk:
        proposal = sdk.knowledge.proposals.show(proposal_id)
    if project and proposal.get("project_id") and str(proposal["project_id"]) != project:
        raise NotFoundError(f"Proposal {proposal_id} is not in project {project}.")
    if machine_output(proposal, json_out=json_out, lines=_proposal_show_lines):
        return
    _print_proposal(proposal, with_payload=True)


def _proposal_show_lines(proposal: dict) -> list[str]:
    """The proposal for an agent: its summary line, the payload as read, the hash last."""
    lines = _proposal_lines([proposal])
    payload = proposal.get("payload")
    if isinstance(payload, dict):
        lines += ["", *(text for text, _style in payload_lines(payload))]
    if proposal.get("content_hash"):
        lines += ["", f"content_hash={proposal['content_hash']}"]
    return lines


@proposals_app.command("accept")
def proposals_accept(
    proposal_id: str = typer.Argument(..., help="Proposal ID."),
    reviewed_hash: str | None = typer.Option(
        None,
        "--reviewed-hash",
        help="The content hash `beakr kb proposals show` printed for the proposal you reviewed.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Accept and apply a wiki proposal, bound to the read you reviewed.

    Pass --reviewed-hash from `beakr kb proposals show`, or run this at a terminal
    without --yes to review the proposal here and confirm it. A proposal that
    changes card table rows is not accepted any other way.
    """
    try:
        given = checked_hash(reviewed_hash)
    except ValueError as exc:
        err_console.print(
            f"{exc} Copy it from `beakr kb proposals show {proposal_id}`.", markup=False
        )
        raise typer.Exit(EXIT_USAGE) from exc
    if given is None and not yes and output_mode(json_out) == "human" and is_interactive():
        # Review here: show the proposal and bind the accept to exactly this read.
        with get_sdk() as sdk:
            proposal = sdk.knowledge.proposals.show(proposal_id) or {}
        _print_proposal(proposal, with_payload=True)
        if not confirmed("Accept and apply this proposal?", False):
            raise typer.Exit(EXIT_ERROR)
        given = proposal.get("content_hash")
    elif given is None:
        # A reviewed hash is the confirmation: it names the exact read that was
        # reviewed, and the server refuses it if the proposal has changed since.
        confirm_or_require_yes(f"Accept and apply proposal {proposal_id}?", yes)
    result = _accept_bound(proposal_id, given)
    if not machine_output(result, json_out=json_out):
        console.print(f"Accepted proposal {result.get('proposal_id', proposal_id)}.", markup=False)


@proposals_app.command("dismiss")
def proposals_dismiss(
    proposal_id: str = typer.Argument(..., help="Proposal ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Dismiss a wiki proposal."""
    confirm_or_require_yes(f"Dismiss proposal {proposal_id}?", yes)
    with get_sdk() as sdk:
        result = sdk.knowledge.proposals.dismiss(proposal_id)
    if not machine_output(result, json_out=json_out):
        console.print(f"Dismissed proposal {result.get('proposal_id', proposal_id)}.", markup=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _title_key(text: str) -> str:
    """A title compared loosely: case, spacing and punctuation ignored (a slug matches)."""
    return "-".join(re.findall(r"[^\W_]+", text.casefold()))


def _resolve_page(
    ref: str,
    *,
    project: str | None = None,
    mine: bool = False,
) -> dict | None:
    """Resolve a page reference (title or UUID) to a page dict.

    Title resolution goes through wiki_page_title_aliases so renamed
    pages still resolve via their previous titles. A page whose title is the
    reference written another way (a slug, ``kestrel-widget``) resolves too.
    Anything else is not found, with the closest titles as suggestions: the
    search's top hit used to be returned silently, so ``kb cat "No Such Page"``
    printed an unrelated page and exited 0.

    The closest titles are the server's own when its 404 names them
    (``error.details.suggestions``, ranked under the caller's access); a
    server without that field gets them from a search.
    """
    suggestions: list[str] = []
    wanted = _title_key(ref)
    scope_id = _project_id(project=project, mine=mine)
    with get_sdk() as sdk:
        pages = sdk.knowledge.pages
        # Try UUID first
        if len(ref) == 36 and "-" in ref:
            try:
                data = pages.get(ref)
                if not _page_matches_scope(data, project=project, mine=mine):
                    raise ValueError("Page is outside the requested scope.")
                return data
            except Exception:
                pass

        # Try exact title (resolves through alias table -- current + previous)
        server_suggestions = None
        try:
            return pages.by_title(ref, project_id=scope_id)
        except NotFoundError as exc:
            reported = exc.details.get("suggestions")
            if isinstance(reported, list):
                server_suggestions = [str(title) for title in reported if title]
        except Exception:
            pass

        if server_suggestions is not None:
            for title in server_suggestions:
                if wanted and _title_key(title) == wanted:
                    return pages.by_title(title, project_id=scope_id)
            suggestions = server_suggestions
            hits = []
        else:
            # An older server names no suggestions: search for them.
            try:
                hits = page_rows(pages.search(ref, project_id=scope_id, limit=5))
            except Exception:
                hits = []
        for hit in hits:
            title = str(hit.get("title") or "")
            if title and title not in suggestions:
                suggestions.append(title)
            if wanted and _title_key(title) == wanted and hit.get("id"):
                data = pages.get(hit["id"])
                if _page_matches_scope(data, project=project, mine=mine):
                    return data

    # Not found (exit 4), the code every other missing object gets.
    if suggestions:
        raise NotFoundError(
            f"Page not found: {ref}",
            fix="Close matches: " + ", ".join(f'"{title}"' for title in suggestions[:5]),
        )
    raise NotFoundError(f"Page not found: {ref}", fix="List pages with `beakr kb ls`.")


def _page_matches_scope(
    page: dict,
    *,
    project: str | None = None,
    mine: bool = False,
) -> bool:
    """Validate page IDs resolved directly still belong to the requested scope."""
    params = _scope_params(project=project, mine=mine)
    project_id = params.get("project_id")
    if project_id and str(page.get("project_id") or "") != str(project_id):
        return False
    return True
