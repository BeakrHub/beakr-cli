"""Knowledge base commands: ls, cat, search, grep, blame, log, sources, provenance, links, timeline, research."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from beakr_cli.client import api_get, api_post, get_personal_space_id, scope_params
from beakr_cli.output import (
    console,
    err_console,
    is_piped,
    print_json,
    print_markdown,
    print_page,
    print_pages_table,
    print_search_results,
    print_table,
)

app = typer.Typer(help="Interact with your Beakr knowledge base.")
propose_app = typer.Typer(help="Create user-approved wiki write proposals.")
proposals_app = typer.Typer(help="Review and apply wiki write proposals.")
app.add_typer(propose_app, name="propose")
app.add_typer(proposals_app, name="proposals")

# Common scope options reused across commands
_project_opt = typer.Option(None, "--project", "-P", help="Project ID to scope the query.")
_space_opt = typer.Option(None, "--space", "-s", help="Space/group ID to scope the query.")
_mine_opt = typer.Option(False, "--mine", "-m", help="Scope to your personal space only.")


def _scoped_get(
    path: str,
    params: dict | None = None,
    *,
    project: str | None = None,
    space: str | None = None,
    mine: bool = False,
):
    """GET with per-call scope override."""
    merged = {**scope_params(space=space, project=project, personal=mine), **(params or {})}
    return api_get(path, merged)


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


@app.command()
def ls(
    page_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by page type."),
    all_scopes: bool = typer.Option(False, "--all", "-a", help="Show pages across all scopes."),
    roots_only: bool = typer.Option(False, "--roots", help="Show only root pages (no parent)."),
    parent: Optional[str] = typer.Option(None, "--parent", "-p", help="Filter to children of this page (slug, title, or UUID)."),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="Max pages to return."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """List knowledge base pages in the current scope."""
    params: dict = {}
    if page_type:
        params["page_type"] = page_type
    if all_scopes:
        params["all"] = "true"
    if roots_only:
        params["roots_only"] = "true"
    if parent:
        parent_data = _resolve_page(parent, project=project, space=space, mine=mine)
        if parent_data:
            params["parent_page_id"] = parent_data["id"]
    if limit is not None:
        params["limit"] = limit

    data = _scoped_get("/v1/knowledge/wiki/pages", params, project=project, space=space, mine=mine)
    pages = data.get("pages", [])

    if json_out or is_piped():
        print_json(pages)
    else:
        print_pages_table(pages)


# ---------------------------------------------------------------------------
# cat
# ---------------------------------------------------------------------------


@app.command()
def cat(
    page: str = typer.Argument(help="Page slug, title, or UUID."),
    rev: Optional[int] = typer.Option(None, "--rev", "-r", help="Specific revision number."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Read a knowledge base page's content."""
    page_data = _resolve_page(page, project=project, space=space, mine=mine)
    if not page_data:
        return

    if rev:
        page_id = page_data["id"]
        revisions = api_get(f"/v1/knowledge/wiki/pages/{page_id}/revisions")
        rev_list = revisions.get("revisions", [])
        match = next((r for r in rev_list if r.get("revision") == rev), None)
        if not match:
            err_console.print(f"Revision {rev} not found.")
            raise typer.Exit(1)
        if json_out:
            print_json(match)
        else:
            print_markdown(match.get("content", ""))
        return

    if json_out:
        print_json(page_data)
    else:
        print_page(page_data)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@app.command()
def search(
    query: str = typer.Argument(help="Search query."),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Semantic + keyword search across knowledge base pages."""
    data = _scoped_get(
        "/v1/knowledge/wiki/search",
        {"q": query, "limit": limit},
        project=project,
        space=space,
        mine=mine,
    )
    results = data.get("results", [])

    if json_out or is_piped():
        print_json(results)
    else:
        print_search_results(results)


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


@app.command()
def grep(
    pattern: str = typer.Argument(help="Keyword or regex pattern."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Search page content by keyword or regex."""
    data = _scoped_get(
        "/v1/knowledge/wiki/search",
        {"q": pattern, "limit": 20},
        project=project,
        space=space,
        mine=mine,
    )
    results = data.get("results", [])

    if json_out or is_piped():
        print_json(results)
    else:
        print_search_results(results)


# ---------------------------------------------------------------------------
# blame
# ---------------------------------------------------------------------------


@app.command()
def blame(
    page: str = typer.Argument(help="Page slug, title, or UUID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show paragraph-level source attribution."""
    page_data = _resolve_page(page, project=project, space=space, mine=mine)
    if not page_data:
        return

    page_id = page_data["id"]
    data = api_get(f"/v1/knowledge/wiki/pages/{page_id}/blame")
    blame_entries = data.get("blame", [])

    if json_out or is_piped():
        print_json(blame_entries)
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
    page: Optional[str] = typer.Argument(None, help="Page slug, title, or UUID. Omit for global."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show revision history for a page, or recent ingestion events globally."""
    if page:
        page_data = _resolve_page(page, project=project, space=space, mine=mine)
        if not page_data:
            return
        page_id = page_data["id"]
        data = api_get(f"/v1/knowledge/wiki/pages/{page_id}/revisions")
        revisions = data.get("revisions", [])

        if json_out or is_piped():
            print_json(revisions)
            return

        columns = ["Rev", "Compiled By", "Summary", "Date"]
        rows = []
        for r in revisions:
            rows.append([
                str(r.get("revision", "")),
                r.get("compiled_by", "")[:30],
                (r.get("summary", "") or "")[:50],
                r.get("created_at", "")[:16] if r.get("created_at") else "",
            ])
        print_table(columns, rows, title=page_data.get("title", ""))
    else:
        data = _scoped_get(
            "/v1/knowledge/wiki/ingestion-events",
            project=project,
            space=space,
            mine=mine,
        )
        events = data.get("events", [])

        if json_out or is_piped():
            print_json(events)
            return

        columns = ["Source", "Status", "Created", "Updated", "Pages", "Date"]
        rows = []
        for e in events:
            titles = ", ".join(e.get("page_titles", []))[:40]
            rows.append([
                e.get("source_title", "")[:30],
                e.get("status", ""),
                str(e.get("pages_created", 0)),
                str(e.get("pages_updated", 0)),
                titles or "-",
                e.get("created_at", "")[:16],
            ])
        print_table(columns, rows, title="Recent Ingestion Events")


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


@app.command()
def sources(
    page: str = typer.Argument(help="Page slug, title, or UUID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show source documents that fed a knowledge base page."""
    page_data = _resolve_page(page, project=project, space=space, mine=mine)
    if not page_data:
        return

    page_id = page_data["id"]
    data = api_get(f"/v1/knowledge/wiki/pages/{page_id}/sources")
    source_list = data.get("sources", [])

    if json_out or is_piped():
        print_json(source_list)
        return

    if not source_list:
        err_console.print("No sources.")
        return

    columns = ["Type", "Title", "ID"]
    rows = []
    for s in source_list:
        rows.append([
            s.get("source_type", ""),
            s.get("source_title", "")[:50],
            s.get("source_id", "")[:36],
        ])
    print_table(columns, rows)


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


@app.command()
def provenance(
    page: str = typer.Argument(help="Page slug, title, or UUID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show section-level citations with stance."""
    page_data = _resolve_page(page, project=project, space=space, mine=mine)
    if not page_data:
        return

    page_id = page_data["id"]
    data = api_get(f"/v1/knowledge/wiki/pages/{page_id}/section-provenance")
    prov = data.get("provenance", {})

    if json_out or is_piped():
        print_json(prov)
        return

    if not prov:
        err_console.print("No section provenance data.")
        return

    for section_id, citations in prov.items():
        console.print(f"\n[bold]{section_id}[/bold]")
        for cit in citations if isinstance(citations, list) else [citations]:
            stance = cit.get("stance", "support")
            source = cit.get("source_title", cit.get("source_id", "unknown"))
            style = {"support": "green", "contradicts": "red", "qualifies": "yellow"}.get(
                stance, "white"
            )
            console.print(f"  [{style}]{stance}[/{style}]  {source}")


# ---------------------------------------------------------------------------
# links (backlinks)
# ---------------------------------------------------------------------------


@app.command()
def links(
    page: str = typer.Argument(help="Page slug, title, or UUID."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show pages that link to this page."""
    page_data = _resolve_page(page, project=project, space=space, mine=mine)
    if not page_data:
        return

    page_id = page_data["id"]
    data = api_get(f"/v1/knowledge/wiki/pages/{page_id}/backlinks")
    backlinks = data.get("backlinks", [])

    if json_out or is_piped():
        print_json(backlinks)
        return

    if not backlinks:
        err_console.print("No backlinks.")
        return

    columns = ["Title", "Slug"]
    rows = [[b.get("title", ""), b.get("slug", "")] for b in backlinks]
    print_table(columns, rows)


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------


@app.command()
def timeline(
    query: Optional[str] = typer.Argument(None, help="Keyword filter (matches section titles, page titles, content)."),
    start_date: Optional[str] = typer.Option(None, "--start", help="Start date YYYY-MM-DD."),
    end_date: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD."),
    page_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by page type."),
    include_content: bool = typer.Option(False, "--content", "-c", help="Include section markdown content."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max events (max 100)."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Query temporal events across the knowledge base by date range and keyword."""
    params: dict = {"limit": min(limit, 100)}
    if query:
        params["q"] = query
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if page_type:
        params["page_type"] = page_type
    if include_content:
        params["include_content"] = "true"

    data = _scoped_get(
        "/v1/knowledge/wiki/timeline",
        params,
        project=project,
        space=space,
        mine=mine,
    )
    events = data.get("events", [])

    if json_out or is_piped():
        print_json(events)
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
        console.print(f"  Page: {e.get('page_title', '')} ({e.get('page_slug', '')})")
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
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Dump the knowledge graph (nodes + edges)."""
    data = _scoped_get("/v1/knowledge/wiki/graph", project=project, space=space, mine=mine)

    if json_out or is_piped():
        print_json(data)
        return

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    console.print(f"{len(nodes)} nodes, {len(edges)} edges")
    for n in nodes[:20]:
        console.print(f"  [{n.get('type', '')}] {n.get('title', n.get('id', ''))}")
    if len(nodes) > 20:
        console.print(f"  ... and {len(nodes) - 20} more")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@app.command()
def stats(
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Show knowledge base statistics for the current scope."""
    data = _scoped_get("/v1/knowledge/stats", project=project, space=space, mine=mine)
    if json_out or is_piped():
        print_json(data)
    else:
        console.print(f"Pages:   {data.get('pages', 0)}")
        console.print(f"Sources: {data.get('sources', 0)}")


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


@app.command()
def research(
    query: str = typer.Argument(help="Question to research."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: Optional[str] = _project_opt,
    space: Optional[str] = _space_opt,
    mine: bool = _mine_opt,
) -> None:
    """Ask a question and get a researched answer with citations."""
    body = {"query": query, **scope_params(space=space, project=project, personal=mine)}
    data = api_post("/v1/knowledge/research", body)

    if json_out or is_piped():
        print_json(data)
        return

    answer = data.get("answer", "")
    if answer:
        print_markdown(answer)

    citations = data.get("citations", [])
    if citations:
        console.print("\n[bold]Sources:[/bold]")
        for i, c in enumerate(citations, 1):
            source_type = c.get("source_type", "")
            title = c.get("title", "")
            excerpt = c.get("excerpt", "")
            line = f"  {i}. [dim][{source_type}][/dim] {title}"
            if excerpt:
                line += f" -- {excerpt[:80]}"
            console.print(line)

    confidence = data.get("confidence", "")
    if confidence:
        console.print(f"\n[dim]Confidence: {confidence}[/dim]")

    gaps = data.get("gaps", "")
    if gaps:
        console.print(f"[dim]Gaps: {gaps}[/dim]")


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
    personal_space_id = get_personal_space_id()
    if not personal_space_id:
        err_console.print("Could not resolve your personal space.")
        raise typer.Exit(1)
    return {"group_id": personal_space_id}


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
    if not yes and not typer.confirm("Accept and apply this proposal?", default=False):
        return
    result = api_post(f"/v1/knowledge/wiki/proposals/{proposal['id']}/accept")
    console.print(f"Accepted proposal {result.get('proposal_id', proposal['id'])}.")


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


@propose_app.command("create")
def propose_create(
    title: str = typer.Option(..., "--title", "-t", help="New page title."),
    content: Optional[str] = typer.Option(None, "--content", "-c", help="New page markdown."),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Read page markdown from file."),
    page_type: str = typer.Option("topic", "--type", help="Page type."),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    sections: Optional[str] = typer.Option(
        None,
        "--sections",
        help="Section metadata JSON array, or @path. Includes citations and event dates.",
    ),
    parent: Optional[str] = typer.Option(None, "--parent", help="Parent page slug, title, or UUID."),
    project: Optional[str] = _project_opt,
    mine: bool = _mine_opt,
    accept: bool = typer.Option(False, "--accept", help="Prompt to accept after creating."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation with --accept."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Propose creating a wiki page."""
    body = {
        "action": "create",
        "title": title,
        "content": _read_content_arg(content, file),
        "page_type": page_type,
        "summary": summary,
        "sections": _load_sections_arg(sections),
        "parent": parent,
        **_proposal_scope(project, mine=mine),
    }
    proposal = api_post("/v1/knowledge/wiki/proposals", body)
    if json_out or is_piped():
        print_json(proposal)
    else:
        _accept_created_proposal(proposal, accept=accept, yes=yes)


@propose_app.command("edit")
def propose_edit(
    page: str = typer.Argument(..., help="Page slug, title, or UUID."),
    content: Optional[str] = typer.Option(None, "--content", "-c", help="Replacement markdown."),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Read replacement markdown from file."),
    patches: Optional[str] = typer.Option(
        None,
        "--patches",
        help="Patch JSON array, or @path to a JSON file.",
    ),
    title: Optional[str] = typer.Option(None, "--title", help="Optional new title."),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    sections: Optional[str] = typer.Option(
        None,
        "--sections",
        help="Section metadata JSON array, or @path. Includes citations and event dates.",
    ),
    project: Optional[str] = _project_opt,
    mine: bool = _mine_opt,
    accept: bool = typer.Option(False, "--accept", help="Prompt to accept after creating."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation with --accept."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Propose editing a wiki page."""
    if patches and (content or file):
        err_console.print("Use either --patches or --content/--file, not both.")
        raise typer.Exit(1)
    body = {
        "action": "edit",
        "page": page,
        "title": title,
        "summary": summary,
        "sections": _load_sections_arg(sections),
        **_proposal_scope(project, mine=mine),
    }
    if patches:
        body["patches"] = _load_json_arg(patches)
    else:
        body["content"] = _read_content_arg(content, file)
    proposal = api_post("/v1/knowledge/wiki/proposals", body)
    if json_out or is_piped():
        print_json(proposal)
    else:
        _accept_created_proposal(proposal, accept=accept, yes=yes)


@propose_app.command("replace")
def propose_replace(
    find: str = typer.Option(..., "--find", help="Text or regex to find."),
    replace: str = typer.Option("", "--replace", help="Replacement text."),
    regex: bool = typer.Option(False, "--regex", help="Treat --find as regex."),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    project: Optional[str] = _project_opt,
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
    proposal = api_post("/v1/knowledge/wiki/proposals", body)
    if json_out or is_piped():
        print_json(proposal)
    else:
        _accept_created_proposal(proposal, accept=accept, yes=yes)


@propose_app.command("move")
def propose_move(
    page: str = typer.Argument(..., help="Page slug, title, or UUID."),
    title: Optional[str] = typer.Option(None, "--title", help="Optional new title."),
    parent: Optional[str] = typer.Option(None, "--parent", help="Optional new parent."),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    project: Optional[str] = _project_opt,
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
    proposal = api_post("/v1/knowledge/wiki/proposals", body)
    if json_out or is_piped():
        print_json(proposal)
    else:
        _accept_created_proposal(proposal, accept=accept, yes=yes)


@propose_app.command("archive")
def propose_archive(
    page: str = typer.Argument(..., help="Page slug, title, or UUID."),
    include_children: bool = typer.Option(True, "--children/--no-children"),
    summary: str = typer.Option("", "--summary", help="Proposal summary."),
    project: Optional[str] = _project_opt,
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
    proposal = api_post("/v1/knowledge/wiki/proposals", body)
    if json_out or is_piped():
        print_json(proposal)
    else:
        _accept_created_proposal(proposal, accept=accept, yes=yes)


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------


@proposals_app.command("list")
def proposals_list(
    status_filter: str = typer.Option("pending", "--status", help="pending, accepted, or dismissed."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max proposals."),
    project: Optional[str] = _project_opt,
    mine: bool = _mine_opt,
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """List wiki proposals."""
    params: dict = {
        "status": status_filter,
        "limit": limit,
        **_proposal_filter_scope(project, mine=mine),
    }
    data = api_get("/v1/knowledge/wiki/proposals", params)
    proposals = data.get("proposals", [])
    if json_out or is_piped():
        print_json(proposals)
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
    proposal = api_get(f"/v1/knowledge/wiki/proposals/{proposal_id}")
    if json_out or is_piped():
        print_json(proposal)
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
    if not yes and not typer.confirm(f"Accept and apply proposal {proposal_id}?", default=False):
        raise typer.Exit()
    result = api_post(f"/v1/knowledge/wiki/proposals/{proposal_id}/accept")
    if json_out or is_piped():
        print_json(result)
    else:
        console.print(f"Accepted proposal {result.get('proposal_id', proposal_id)}.")


@proposals_app.command("dismiss")
def proposals_dismiss(
    proposal_id: str = typer.Argument(..., help="Proposal ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Dismiss a wiki proposal."""
    if not yes and not typer.confirm(f"Dismiss proposal {proposal_id}?", default=False):
        raise typer.Exit()
    result = api_post(f"/v1/knowledge/wiki/proposals/{proposal_id}/dismiss")
    if json_out or is_piped():
        print_json(result)
    else:
        console.print(f"Dismissed proposal {result.get('proposal_id', proposal_id)}.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_page(
    ref: str,
    *,
    project: str | None = None,
    space: str | None = None,
    mine: bool = False,
) -> dict | None:
    """Resolve a page reference (slug, title, or UUID) to a page dict."""
    # Try UUID first
    if len(ref) == 36 and "-" in ref:
        try:
            data = api_get(f"/v1/knowledge/wiki/pages/{ref}")
            if not _page_matches_scope(data, project=project, space=space, mine=mine):
                raise ValueError("Page is outside the requested scope.")
            return data
        except Exception:
            pass

    # Try slug
    try:
        data = api_get(
            f"/v1/knowledge/wiki/pages/by-slug/{ref}",
            scope_params(space=space, project=project, personal=mine),
        )
        return data
    except Exception:
        pass

    # Try search by title
    try:
        results = api_get(
            "/v1/knowledge/wiki/search",
            {"q": ref, "limit": 1, **scope_params(space=space, project=project, personal=mine)},
        )
        hits = results.get("results", [])
        if hits:
            page_id = hits[0].get("id")
            if page_id:
                data = api_get(f"/v1/knowledge/wiki/pages/{page_id}")
                if not _page_matches_scope(data, project=project, space=space, mine=mine):
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
    space: str | None = None,
    mine: bool = False,
) -> bool:
    """Validate page IDs resolved directly still belong to the requested scope."""
    params = scope_params(space=space, project=project, personal=mine)
    project_id = params.get("project_id")
    group_id = params.get("group_id")
    if project_id and str(page.get("project_id") or "") != str(project_id):
        return False
    if group_id and str(page.get("group_id") or "") != str(group_id):
        return False
    return True
