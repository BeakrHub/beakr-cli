"""Knowledge base commands."""

from __future__ import annotations

import json
import sys
from itertools import islice
from pathlib import Path

import typer
from beakr_sdk.resources.knowledge import result_text

from beakr_cli.citations import CitationValidationError, validate_proposal_sections
from beakr_cli.cited_answer import print_research_result, research_lines
from beakr_cli.output import (
    console,
    err_console,
    machine_output,
    output_mode,
    page_lines,
    page_rows,
    print_json,
    print_markdown,
    print_page,
    print_pages_table,
    print_search_results,
    print_table,
)
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes, confirmed
from beakr_cli.scope import get_personal_project_id, scope_params
from beakr_cli.sdk import get_sdk
from beakr_cli.source_listing import document_lines, group_by_document

app = typer.Typer(help="Interact with your Beakr knowledge base.")
propose_app = typer.Typer(help="Create user-approved wiki write proposals.")
proposals_app = typer.Typer(help="Review and apply wiki write proposals.")
app.add_typer(propose_app, name="propose")
app.add_typer(proposals_app, name="proposals")

# Common scope options reused across commands
_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
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


def _scope_params(*, project: str | None = None, mine: bool = False) -> dict:
    """Build scope params and convert validation errors to CLI errors."""
    try:
        return scope_params(project=project, personal=mine)
    except ValueError as exc:
        err_console.print(str(exc))
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
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Read a knowledge base page's content."""
    page_data = _resolve_page(page, project=project, mine=mine)
    if not page_data:
        return

    if rev is not None:
        # The Knowledge door's `cat` reads a past revision (`rev`).
        with get_sdk() as sdk:
            result = sdk.knowledge.cat(
                str(page_data["id"]), rev=rev, scope=_project_id(project=project, mine=mine)
            )
        if json_out:
            print_json(result)
        else:
            print_markdown(result_text(result))
        return

    with get_sdk() as sdk:
        source_list = sdk.knowledge.pages.sources(str(page_data["id"]))
    if machine_output(
        {**page_data, "sources": source_list},
        json_out=json_out,
        lines=lambda data: (
            [*page_lines(data), "", "sources:", *document_lines(source_list)]
            if source_list
            else page_lines(data)
        ),
    ):
        return
    print_page(page_data)
    if source_list:
        console.print("\n[bold]Sources[/bold]")
        _print_documents(source_list)


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
        snippet = " ".join(str(hit.get("content_snippet") or hit.get("summary") or "").split())
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
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
) -> None:
    """Find the lines that contain a word, id or exact name, with the section they are in."""
    _door_read(
        "grep",
        project=project,
        mine=mine,
        json_out=json_out,
        query=pattern,
        mode="regex" if regex else None,
        context_lines=context,
        limit=limit,
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
        console.print(f"[dim]rev {rev}[/dim]  [cyan]{source[:40]}[/cyan]  {display}")


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

        if machine_output(revisions, json_out=json_out):
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
        # The Knowledge door's `log` with no page: recent revisions in scope.
        with get_sdk() as sdk:
            result = sdk.knowledge.command("log", scope=_project_id(project=project, mine=mine))
        if machine_output(result, json_out=json_out):
            return
        console.print(result_text(result), markup=False)


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

        console.print(f"\n[bold]{title}[/bold]")
        for cit in citations:
            stance = cit.get("stance", "support")
            source = cit.get("source_title", cit.get("source_id", "unknown"))
            style = {"support": "green", "contradicts": "red", "qualifies": "yellow"}.get(
                stance, "white"
            )
            inline = " inline" if cit.get("inline") else ""
            key = f" ({cit.get('key')})" if cit.get("key") else ""
            console.print(f"  [{style}]{stance}{inline}[/{style}]  {source}{key}")


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
            f"[dim]{date_str}[/dim]  [cyan][{e.get('page_type', '')}][/cyan]  "
            f"[bold]{e.get('title', '')}[/bold]"
        )
        console.print(f"  Page: {e.get('page_title', '')}")
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
        console.print(f"  ... and {len(nodes) - 20} more")


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
        console.print(f"Pages:   {data.get('pages', 0)}")
        console.print(f"Sources: {data.get('sources', 0)}")


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
        data = sdk.knowledge.research(query, project_id=project_id, quick=quick)

    if machine_output(data, json_out=json_out, lines=research_lines):
        return

    print_research_result(data)


# ---------------------------------------------------------------------------
# proposal helpers
# ---------------------------------------------------------------------------


def _proposal_scope(project: str | None, *, mine: bool = False) -> dict:
    """Build required explicit scope for write proposals."""
    selected = sum(bool(v) for v in (project, mine))
    if selected != 1:
        err_console.print("Provide exactly one of --project or --mine for write proposals.")
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
        err_console.print(f"Invalid --sections:\n{exc}")
        raise typer.Exit(1) from exc


def _create_proposal(body: dict) -> dict:
    """Stage one of the CLI's proposal shapes (``create``, ``edit``, ``mv`` ...)."""
    fields = dict(body)
    action = fields.pop("action")
    with get_sdk() as sdk:
        return sdk.knowledge.proposals.create(action, **fields)


def _print_proposal(proposal: dict) -> None:
    """Print a concise proposal summary."""
    console.print(f"[bold]Proposal:[/bold] {proposal.get('id')}")
    console.print(f"Type:   {proposal.get('proposal_type')}")
    console.print(f"Status: {proposal.get('status')}")
    if proposal.get("page_title"):
        console.print(f"Page:   {proposal.get('page_title')}")
    if proposal.get("summary"):
        console.print(f"Summary: {proposal.get('summary')}")
    payload = proposal.get("payload") or {}
    if proposal.get("proposal_type") == "find_replace":
        console.print(
            f"Changes: {payload.get('total_matches', 0)} match(es) across "
            f"{payload.get('total_pages', 0)} page(s)"
        )
    elif proposal.get("proposal_type") == "archive":
        console.print(f"Pages:  {len(payload.get('archive_items') or [])}")
    elif proposal.get("proposal_type") == "mv":
        console.print(f"Moves:  {len(payload.get('reorg') or [])}")


def _accept_created_proposal(proposal: dict, *, accept: bool, yes: bool) -> None:
    _print_proposal(proposal)
    if not accept:
        return
    # The proposal exists either way: a "no" leaves it pending, not failed.
    if not confirmed("Accept and apply this proposal?", yes):
        return
    with get_sdk() as sdk:
        result = sdk.knowledge.proposals.accept(proposal["id"])
    console.print(f"Accepted proposal {result.get('proposal_id', proposal['id'])}.")


def _finish_proposal(proposal: dict, *, accept: bool, yes: bool, json_out: bool) -> None:
    """Show (or print as JSON) a created proposal and accept it when asked.

    ``--accept --yes`` accepts in every output mode. JSON and piped output used
    to skip the accept step entirely, so a CI job asking for ``--accept --yes``
    left the proposal pending and still exited 0. Without ``--yes`` there is no
    one to confirm in those modes, so ``--accept`` alone is refused rather than
    ignored.
    """
    if output_mode(json_out) == "human":
        _accept_created_proposal(proposal, accept=accept, yes=yes)
        return
    accept_hint = f"beakr kb proposals accept {proposal['id']}"
    if accept and not yes:
        machine_output(proposal, json_out=json_out, hint=accept_hint)
        err_console.print(
            "Not accepted: --accept needs --yes when output is JSON or piped. "
            f"Accept with `{accept_hint}`."
        )
        raise typer.Exit(1)
    if accept:
        with get_sdk() as sdk:
            result = sdk.knowledge.proposals.accept(proposal["id"])
        machine_output({**proposal, "accepted": True, "accept_result": result}, json_out=json_out)
        return
    machine_output(proposal, json_out=json_out, hint=accept_hint)


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


@propose_app.command("create")
def propose_create(
    title: str = typer.Option(..., "--title", "-t", help="New page title."),
    content: str | None = typer.Option(None, "--content", "-c", help="New page markdown."),
    file: Path | None = typer.Option(None, "--file", "-f", help="Read page markdown from file."),
    page_type: str = typer.Option("topic", "--type", help="Page type."),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    sections: str | None = _sections_opt,
    parent: str | None = typer.Option(None, "--parent", help="Parent page title or UUID."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    accept: bool = typer.Option(False, "--accept", help="Prompt to accept after creating."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation with --accept."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Propose creating a wiki page."""
    page_content = _read_content_arg(content, file)
    section_data = _load_sections_arg(sections)
    _validate_sections_arg(page_content, section_data, require_content=True)
    body = {
        "action": "create",
        "title": title,
        "content": page_content,
        "page_type": page_type,
        "summary": summary,
        "sections": section_data,
        "parent": parent,
        **_proposal_scope(project, mine=mine),
    }
    proposal = _create_proposal(body)
    _finish_proposal(proposal, accept=accept, yes=yes, json_out=json_out)


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
    section_data = _load_sections_arg(sections)
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
        help="pending, accepted, or dismissed.",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max proposals."),
    project: str | None = _project_opt,
    mine: bool = _mine_opt,
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """List wiki proposals."""
    params: dict = {
        "status": status_filter,
        "limit": limit,
        **_proposal_filter_scope(project, mine=mine),
    }
    with get_sdk() as sdk:
        proposals = sdk.knowledge.proposals.list(**params)
    if machine_output(proposals, json_out=json_out):
        return
    rows = [
        [
            p.get("id", "")[:8],
            p.get("proposal_type", ""),
            p.get("status", ""),
            p.get("page_title", "")[:32],
            p.get("summary", "")[:48],
        ]
        for p in proposals
    ]
    print_table(["ID", "Type", "Status", "Page", "Summary"], rows, title="Wiki Proposals")


@proposals_app.command("show")
def proposals_show(
    proposal_id: str = typer.Argument(..., help="Proposal ID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Show one wiki proposal."""
    with get_sdk() as sdk:
        proposal = sdk.knowledge.proposals.show(proposal_id)
    if machine_output(proposal, json_out=json_out):
        return
    _print_proposal(proposal)
    console.print()
    print_json(proposal.get("payload", {}))


@proposals_app.command("accept")
def proposals_accept(
    proposal_id: str = typer.Argument(..., help="Proposal ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Accept and apply a wiki proposal."""
    confirm_or_require_yes(f"Accept and apply proposal {proposal_id}?", yes)
    with get_sdk() as sdk:
        result = sdk.knowledge.proposals.accept(proposal_id)
    if not machine_output(result, json_out=json_out):
        console.print(f"Accepted proposal {result.get('proposal_id', proposal_id)}.")


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
        console.print(f"Dismissed proposal {result.get('proposal_id', proposal_id)}.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_page(
    ref: str,
    *,
    project: str | None = None,
    mine: bool = False,
) -> dict | None:
    """Resolve a page reference (title or UUID) to a page dict.

    Title resolution goes through wiki_page_title_aliases so renamed
    pages still resolve via their previous titles. A fuzzy search
    fallback handles partial input.
    """
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
        try:
            return pages.by_title(ref, project_id=_project_id(project=project, mine=mine))
        except Exception:
            pass

        # Try fuzzy search (handles partial input and slug-shaped legacy refs)
        try:
            results = pages.search(ref, project_id=_project_id(project=project, mine=mine), limit=1)
            hits = page_rows(results)
            if hits:
                page_id = hits[0].get("id")
                if page_id:
                    data = pages.get(page_id)
                    if not _page_matches_scope(data, project=project, mine=mine):
                        raise ValueError("Page is outside the requested scope.")
                    return data
        except Exception:
            pass

    err_console.print(f"Page not found: {ref}")
    raise typer.Exit(1)


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
