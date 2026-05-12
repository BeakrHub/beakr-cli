"""MCP server exposing Beakr knowledge base tools for Claude Code and other AI assistants."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from beakr_cli.citations import CitationValidationError, validate_proposal_sections
from beakr_cli.client import get_async_client, scope_params

mcp = FastMCP(
    "beakr",
    instructions=(
        "Beakr is the team's organizational memory and connected services. "
        "Teams write decisions, processes, ownership, and dated events here so "
        "that 6 months later the answer to 'why did we do X' is one search "
        "away instead of lost in Slack archives or tribal knowledge.\n\n"
        "RESPONSIBILITIES:\n"
        "Use Beakr to answer questions about the user's org with citations. "
        "When the conversation produces something durable (a decision with "
        "rationale, a named process, an ownership assignment, a dated event, "
        "a key relationship), propose adding it -- don't wait to be asked. "
        "First check with kb_search that it isn't already captured, then "
        "surface a brief suggestion before writing ('this seems worth "
        "capturing -- want me to propose it?'). Never auto-write; always go "
        "through proposals and wait for explicit acceptance. Cite the current "
        "session as a 'conversation' inline source with meta.excerpt when the "
        "information originates from this chat.\n\n"
        "TOOLS:\n"
        "- 'research': Primary tool. Ask any question and get a cited answer from the "
        "wiki, documents, and connected services (Slack, Gmail, Calendar, Jira, etc.). "
        "Use this first for most questions.\n"
        "- 'kb_*' tools: Direct access to knowledge base pages. Use for browsing, "
        "reading specific pages, or inspecting sources/provenance.\n\n"
        "SCOPING:\n"
        "Beakr organizes knowledge into projects, including each user's personal project. "
        "Most read tools accept 'project_id' or 'personal_only=true'; use one whenever "
        "the user is working in a specific project. Write "
        "proposal tools require exactly one of project_id or personal=true. "
        "Use list_projects to discover available project IDs.\n\n"
        "WRITES:\n"
        "All wiki writes go through proposals. Create proposals with kb_propose_* "
        "tools, show/list them for review, and only call kb_accept_proposal after "
        "the user explicitly asks to accept/apply that specific proposal. Proposal "
        "sections can include event metadata and citations. Section IDs must match "
        "<!-- sec:ID --> markers in content; event dates should be full YYYY-MM-DD "
        "dates. Put inline citation tokens like {{source_type:source_id}} or "
        "{{!source_type:source_id}} directly in wiki markdown after every factual "
        "claim, table row/value, date, title, and relationship. Use the same "
        "source keys in sections[].citations with stance so section provenance "
        "can roll up support, qualification, and contradiction. Proposal tools "
        "validate that inline tokens and sections[].citations match. Citations can "
        "reference existing Beakr sources from kb_sources or kb_provenance, "
        "external identifiers, or inline source_type 'conversation', "
        "'agent_note', or 'user_note' with source_title and meta.excerpt/content/text. "
        "Section objects use this shape: "
        "{id, title, event_start, event_end, date_precision, citations:["
        "{key, source_type, source_id, source_title, stance, chunk_ref, meta}]}."
    ),
)

async def _get(
    path: str,
    params: dict | None = None,
    *,
    project: str | None = None,
    project_id: str | None = None,
    personal: bool = False,
) -> dict:
    merged = {
        **(await _scope_params(project=project, project_id=project_id, personal=personal)),
        **(params or {}),
    }
    async with get_async_client() as c:
        resp = await c.get(path, params=merged)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict | None = None) -> dict:
    async with get_async_client() as c:
        resp = await c.post(path, json=body)
        resp.raise_for_status()
        return resp.json()


def _validate_sections(
    content: str | None,
    sections: list[dict],
    *,
    require_content: bool,
) -> None:
    try:
        validate_proposal_sections(content, sections, require_content=require_content)
    except CitationValidationError as exc:
        raise ValueError(f"Invalid sections:\n{exc}") from exc


async def _patch(path: str, body: dict | None = None) -> dict:
    async with get_async_client() as c:
        resp = await c.patch(path, json=body)
        resp.raise_for_status()
        return resp.json()


async def _proposal_scope(project_id: str, personal: bool) -> dict:
    if bool(project_id) == bool(personal):
        raise ValueError("Provide exactly one of project_id or personal=true.")
    if project_id:
        return {"project_id": project_id}
    personal_project_id = await _get_personal_project_id()
    if not personal_project_id:
        raise ValueError("Could not resolve the user's personal project.")
    return {"project_id": personal_project_id}


async def _scope_params(
    *,
    project: str | None = None,
    project_id: str | None = None,
    personal: bool = False,
) -> dict:
    explicit_project = project_id or project
    if personal:
        if explicit_project:
            raise ValueError("Provide at most one of project_id or personal=true.")
        personal_project_id = await _get_personal_project_id()
        if not personal_project_id:
            raise ValueError("Could not resolve the user's personal project.")
        return {"project_id": personal_project_id}
    return scope_params(project=explicit_project)


_personal_project_id: str | None = None


async def _get_personal_project_id() -> str:
    global _personal_project_id
    if _personal_project_id is not None:
        return _personal_project_id
    async with get_async_client() as c:
        resp = await c.get("/v1/projects")
        resp.raise_for_status()
        data = resp.json()
    projects = data if isinstance(data, list) else data.get("projects", data)
    for project in projects or []:
        if project.get("project_type") == "personal":
            _personal_project_id = str(project.get("id") or "")
            return _personal_project_id
    return ""


async def _proposal_filter_scope(project_id: str, personal: bool) -> dict:
    if project_id and personal:
        raise ValueError("Provide at most one of project_id or personal=true.")
    if not project_id and not personal:
        return {}
    return await _proposal_scope(project_id, personal)


def _page_matches_scope(
    page: dict,
    *,
    project_id: str = "",
) -> bool:
    if project_id and str(page.get("project_id") or "") != str(project_id):
        return False
    return True


def _format_proposal(data: dict) -> str:
    payload = data.get("payload") or {}
    lines = [
        f"Proposal {data.get('id')}",
        f"Type: {data.get('proposal_type')}",
        f"Status: {data.get('status')}",
    ]
    if data.get("page_title"):
        lines.append(f"Page: {data['page_title']}")
    if data.get("summary"):
        lines.append(f"Summary: {data['summary']}")
    if data.get("proposal_type") == "find_replace":
        lines.append(
            f"Changes: {payload.get('total_matches', 0)} match(es) across "
            f"{payload.get('total_pages', 0)} page(s)"
        )
    elif data.get("proposal_type") == "archive":
        lines.append(f"Pages: {len(payload.get('archive_items') or [])}")
    elif data.get("proposal_type") == "mv":
        lines.append(f"Moves: {len(payload.get('reorg') or [])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Workspace discovery
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_projects() -> str:
    """List projects in the organization.

    Use this to discover available projects and their IDs.
    Pass the project ID to kb tools via the project_id parameter.
    """
    async with get_async_client() as c:
        resp = await c.get("/v1/projects")
        resp.raise_for_status()
        data = resp.json()
    projects = data if isinstance(data, list) else data.get("projects", data)
    if not projects:
        return "No projects found."
    lines = []
    for p in projects:
        name = p.get("name", "Untitled")
        pid = p.get("id", "")
        project_type = p.get("project_type", "standard")
        desc = p.get("description", "") or ""
        lines.append(f"- {name} ({project_type}, id: {pid}){f' -- {desc}' if desc else ''}")
    return "\n".join(lines)


@mcp.tool()
async def get_profile(
    profile_type: str = "org",
    project_id: str = "",
) -> str:
    """Get the knowledge profile for an org or project.

    Profiles contain goals, focus areas, core people/organizations/topics,
    and extraction guidance that shape how knowledge is organized.

    Args:
        profile_type: One of 'org' or 'project'.
        project_id: Project ID (required if profile_type is 'project').
    """
    if profile_type == "org":
        data = await _get("/v1/knowledge/profiles/org")
    elif profile_type == "project" and project_id:
        data = await _get(f"/v1/knowledge/profiles/project/{project_id}")
    else:
        return f"Invalid: profile_type='{profile_type}' requires profile_type='org' or project_id."

    if not data:
        return f"No {profile_type} profile found."

    lines = [f"# {data.get('name', 'Untitled')} ({profile_type} profile)"]
    if data.get("description"):
        lines.append(data["description"])
    if data.get("goals"):
        lines.append("\nGoals: " + ", ".join(data["goals"]))
    if data.get("focus_areas"):
        lines.append("Focus areas: " + ", ".join(data["focus_areas"]))
    if data.get("core_people"):
        lines.append("\nKey people:")
        for p in data["core_people"]:
            lines.append(f"  - {p.get('name', '')} ({p.get('role', '')})")
    if data.get("core_organizations"):
        lines.append("\nKey organizations:")
        for o in data["core_organizations"]:
            lines.append(f"  - {o.get('name', '')} ({o.get('type', '')})")
    if data.get("core_topics"):
        lines.append("\nKey topics:")
        for t in data["core_topics"]:
            lines.append(f"  - {t.get('name', '')}")
    if data.get("extraction_guidance"):
        lines.append(f"\nGuidance: {data['extraction_guidance']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Knowledge base tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def kb_ls(
    page_type: str | None = None,
    roots_only: bool = False,
    parent_page: str | None = None,
    limit: int | None = None,
    personal_only: bool = False,
    project: str | None = None,
    project_id: str = "",
) -> str:
    """List all knowledge base pages in scope.

    Args:
        page_type: Filter by type.
        roots_only: If true, return only root pages.
        parent_page: Filter to children of this page (title or UUID).
        limit: Max pages to return.
        personal_only: If true, only show pages in the user's personal project.
        project_id: Project ID to scope the query. Use list_projects to discover IDs.
    """
    params: dict = {"all": "true"}
    if page_type:
        params["page_type"] = page_type
    if roots_only:
        params["roots_only"] = "true"
    if parent_page:
        parent = await _resolve_page(
            parent_page,
            project=project,
            project_id=project_id,
            personal=personal_only,
        )
        params["parent_page_id"] = parent["id"]
    if limit is not None:
        params["limit"] = limit
    data = await _get(
        "/v1/knowledge/wiki/pages",
        params,
        project=project,
        project_id=project_id,
        personal=personal_only,
    )
    pages = data.get("pages", [])
    lines = []
    for p in pages:
        title = p.get("title", "Untitled")
        page_type = p.get("page_type", "")
        lines.append(f"- {title} ({page_type})")
    return "\n".join(lines) if lines else "No pages found."


@mcp.tool()
async def kb_cat(
    page: str,
    personal_only: bool = False,
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Read a knowledge base page's full content.

    Args:
        page: Page title or UUID.
        personal_only: If true, only resolve pages in the user's personal project.
        project_id: Project ID to scope the query.
    """
    page_data = await _resolve_page(
        page,
        project=project,
        project_id=project_id,
        personal=personal_only,
    )
    title = page_data.get("title", "Untitled")
    content = page_data.get("content", "")
    rev = page_data.get("revision", "?")
    page_type = page_data.get("page_type", "")
    return f"# {title}\nType: {page_type}  |  Revision: {rev}\n\n{content}"


@mcp.tool()
async def kb_search(
    query: str,
    limit: int = 10,
    personal_only: bool = False,
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Search knowledge base pages by semantic similarity and keyword match.

    Args:
        query: Search query text.
        limit: Maximum number of results (default 10, max 20).
        personal_only: If true, only search the user's personal project.
        project_id: Project ID to scope the search.
    """
    data = await _get(
        "/v1/knowledge/wiki/search",
        {"q": query, "limit": min(limit, 20)},
        project=project,
        project_id=project_id,
        personal=personal_only,
    )
    results = data.get("results", [])
    if not results:
        return "No results found."
    lines = []
    for r in results:
        title = r.get("title", "Untitled")
        snippet = r.get("snippet", r.get("summary", ""))[:200]
        lines.append(f"## {title}\n{snippet}\n")
    return "\n".join(lines)


@mcp.tool()
async def kb_grep(
    pattern: str,
    personal_only: bool = False,
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Search knowledge base page content by keyword or pattern.

    Args:
        pattern: Keyword or search pattern.
        personal_only: If true, only search the user's personal project.
        project_id: Project ID to scope the search.
    """
    data = await _get(
        "/v1/knowledge/wiki/search",
        {"q": pattern, "limit": 20},
        project=project,
        project_id=project_id,
        personal=personal_only,
    )
    results = data.get("results", [])
    if not results:
        return "No matches found."
    lines = []
    for r in results:
        title = r.get("title", "Untitled")
        lines.append(f"- {title}")
    return "\n".join(lines)


@mcp.tool()
async def kb_blame(
    page: str,
    personal_only: bool = False,
    project_id: str = "",
) -> str:
    """Show paragraph-level source attribution for a knowledge base page.

    Args:
        page: Page title or UUID.
        personal_only: If true, only resolve pages in the user's personal project.
        project_id: Project ID to scope the query.
    """
    page_data = await _resolve_page(
        page,
        project_id=project_id,
        personal=personal_only,
    )
    page_id = page_data["id"]
    data = await _get(f"/v1/knowledge/wiki/pages/{page_id}/blame")
    entries = data.get("blame", [])
    if not entries:
        return "No blame data available."
    lines = []
    for e in entries:
        source = e.get("source_title", e.get("compiled_by", "unknown"))
        rev = e.get("revision", "?")
        text = e.get("text", "").strip()[:120]
        lines.append(f"[rev {rev}] {source}: {text}")
    return "\n".join(lines)


@mcp.tool()
async def kb_sources(
    page: str,
    personal_only: bool = False,
    project_id: str = "",
) -> str:
    """List source documents that contributed to a knowledge base page.

    Args:
        page: Page title or UUID.
        personal_only: If true, only resolve pages in the user's personal project.
        project_id: Project ID to scope the query.
    """
    page_data = await _resolve_page(
        page,
        project_id=project_id,
        personal=personal_only,
    )
    page_id = page_data["id"]
    data = await _get(f"/v1/knowledge/wiki/pages/{page_id}/sources")
    sources = data.get("sources", [])
    if not sources:
        return "No sources."
    lines = []
    for s in sources:
        source_type = s.get("source_type", "")
        title = s.get("source_title", s.get("source_id", ""))
        lines.append(f"- [{source_type}] {title}")
    return "\n".join(lines)


@mcp.tool()
async def kb_provenance(
    page: str,
    personal_only: bool = False,
    project_id: str = "",
) -> str:
    """Show section provenance rollups with inline citations and stance.

    Args:
        page: Page title or UUID.
        personal_only: If true, only resolve pages in the user's personal project.
        project_id: Project ID to scope the query.
    """
    page_data = await _resolve_page(
        page,
        project_id=project_id,
        personal=personal_only,
    )
    page_id = page_data["id"]
    data = await _get(f"/v1/knowledge/wiki/pages/{page_id}/section-provenance")
    prov = data.get("provenance", {})
    if not prov:
        return "No section provenance data."
    lines = []
    for section_id, section_data in prov.items():
        title = (
            section_data.get("title", section_id)
            if isinstance(section_data, dict)
            else section_id
        )
        lines.append(f"\n## {title or section_id}")
        sources = section_data.get("sources", []) if isinstance(section_data, dict) else []
        if not sources:
            lines.append("  (no citations)")
            continue
        for c in sources:
            stance = c.get("stance", "support")
            source = c.get("source_title") or c.get("source_id", "unknown")
            inline = " inline" if c.get("inline") else ""
            key = f" ({c.get('key')})" if c.get("key") else ""
            lines.append(f"  [{stance}{inline}] {source}{key}")
    return "\n".join(lines)


@mcp.tool()
async def kb_links(
    page: str,
    personal_only: bool = False,
    project_id: str = "",
) -> str:
    """Show pages that link to this page.

    Args:
        page: Page title or UUID.
        personal_only: If true, only resolve pages in the user's personal project.
        project_id: Project ID to scope the query.
    """
    page_data = await _resolve_page(
        page,
        project_id=project_id,
        personal=personal_only,
    )
    page_id = page_data["id"]
    data = await _get(f"/v1/knowledge/wiki/pages/{page_id}/backlinks")
    backlinks = data.get("backlinks", [])
    if not backlinks:
        return "No backlinks."
    lines = [f"- {b.get('title', '')}" for b in backlinks]
    return "\n".join(lines)


@mcp.tool()
async def kb_timeline(
    query: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page_type: str | None = None,
    include_content: bool = False,
    limit: int = 50,
    personal_only: bool = False,
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Query temporal events (decisions, meetings, etc.) across the knowledge base.

    Filter by keyword, date range, and/or page type. Returns events sorted
    chronologically with their page context. Use this to trace how a topic,
    project, or entity evolved over time.

    Args:
        query: Keyword filter -- matches section titles, page titles, and content.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        page_type: Filter by type (decision, meeting, topic, person, etc.).
        include_content: If true, return each section's full markdown content.
        limit: Max events to return (default 50, max 100).
        personal_only: If true, only show events from the user's personal project.
        project_id: Project ID to scope the query.
    """
    params: dict = {"limit": min(limit, 100)}
    if query:
        params["q"] = query
    if include_content:
        params["include_content"] = "true"
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if page_type:
        params["page_type"] = page_type
    data = await _get(
        "/v1/knowledge/wiki/timeline",
        params,
        project=project,
        project_id=project_id,
        personal=personal_only,
    )
    events = data.get("events", [])
    if not events:
        return "No events found in the specified range."
    lines = []
    for e in events:
        date_str = e.get("event_start", "")
        if e.get("event_end") and e["event_end"] != date_str:
            date_str += f" -- {e['event_end']}"
        precision = e.get("date_precision", "")
        if precision and precision != "day":
            date_str += f" ({precision})"
        ptype = e.get("page_type", "")
        title = e.get("title", "")
        page_title = e.get("page_title", "")
        lines.append(f"{date_str} [{ptype}] {title}")
        lines.append(f"  Page: {page_title}")
        if e.get("content"):
            lines.append(f"\n{e['content']}\n")
    return "\n".join(lines)


@mcp.tool()
async def kb_log(
    page: str | None = None,
    personal_only: bool = False,
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Show revision history for a page, or recent ingestion events if no page given.

    Args:
        page: Page title or UUID. Omit for global ingestion log.
        personal_only: If true, only show events from the user's personal project.
        project_id: Project ID to scope the ingestion log.
    """
    if page:
        page_data = await _resolve_page(
            page,
            project=project,
            project_id=project_id,
            personal=personal_only,
        )
        page_id = page_data["id"]
        data = await _get(f"/v1/knowledge/wiki/pages/{page_id}/revisions")
        revisions = data.get("revisions", [])
        if not revisions:
            return "No revisions."
        lines = []
        for r in revisions:
            rev = r.get("revision", "?")
            by = r.get("compiled_by", "")
            summary = r.get("summary", "") or ""
            date = (r.get("created_at", "") or "")[:16]
            lines.append(f"rev {rev}  {by}  {date}  {summary}")
        return "\n".join(lines)
    else:
        data = await _get(
            "/v1/knowledge/wiki/ingestion-events",
            project=project,
            project_id=project_id,
            personal=personal_only,
        )
        events = data.get("events", [])
        if not events:
            return "No recent ingestion events."
        lines = []
        for e in events:
            titles = ", ".join(e.get("page_titles", []))
            lines.append(
                f"[{e.get('status', '')}] {e.get('source_title', '')} "
                f"({e.get('pages_created', 0)} created, {e.get('pages_updated', 0)} updated) "
                f"pages: {titles or '-'}  {e.get('created_at', '')[:16]}"
            )
        return "\n".join(lines)


@mcp.tool()
async def kb_stats(
    personal_only: bool = False,
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Show knowledge base statistics (page count, source count) for the current scope.

    Args:
        personal_only: If true, only count pages in the user's personal project.
        project_id: Project ID to scope the stats.
    """
    data = await _get(
        "/v1/knowledge/stats",
        project=project,
        project_id=project_id,
        personal=personal_only,
    )
    return f"Pages: {data.get('pages', 0)}\nSources: {data.get('sources', 0)}"


@mcp.tool()
async def kb_graph(
    personal_only: bool = False,
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Get a summary of the knowledge base graph structure.

    Returns the top connected pages and basic graph stats. Use kb_links
    on a specific page to explore its neighborhood.

    Args:
        personal_only: If true, only show the user's personal project graph.
        project_id: Project ID to scope the graph.
    """
    data = await _get(
        "/v1/knowledge/wiki/graph",
        {"max_nodes": "30"},
        project=project,
        project_id=project_id,
        personal=personal_only,
    )
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Count connections per node
    connections: dict[str, int] = {}
    for e in edges:
        connections[e["from"]] = connections.get(e["from"], 0) + 1
        connections[e["to"]] = connections.get(e["to"], 0) + 1

    # Build summary
    lines = [f"Graph: {len(nodes)} pages, {len(edges)} links\n"]
    lines.append("Top connected pages:")
    node_map = {n["id"]: n for n in nodes}
    ranked = sorted(connections.items(), key=lambda x: x[1], reverse=True)[:15]
    for node_id, count in ranked:
        n = node_map.get(node_id, {})
        label = n.get("label", node_id[:8])
        ptype = n.get("page_type", "")
        lines.append(f"  {label} ({ptype}) - {count} links")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_page(
    ref: str,
    *,
    project: str | None = None,
    project_id: str = "",
    personal: bool = False,
) -> dict:
    """Resolve a page reference to a full page dict."""
    scope = await _scope_params(project=project, project_id=project_id, personal=personal)
    scoped_project_id = str(scope.get("project_id") or "")

    # Try UUID
    if len(ref) == 36 and "-" in ref:
        try:
            page = await _get(f"/v1/knowledge/wiki/pages/{ref}")
            if _page_matches_scope(
                page,
                project_id=scoped_project_id,
            ):
                return page
        except Exception:
            pass

    # Try title (resolves via wiki_page_title_aliases -- both current and
    # previous titles match, so renamed pages still resolve).
    try:
        return await _get(
            "/v1/knowledge/wiki/pages/by-title",
            {"title": ref},
            project=project,
            project_id=project_id,
            personal=personal,
        )
    except Exception:
        pass

    # Try fuzzy search (handles partial input and slug-shaped legacy refs
    # like "marcos-ortiz" by ranking on trigram + FTS).
    try:
        results = await _get(
            "/v1/knowledge/wiki/search",
            {"q": ref, "limit": 1},
            project=project,
            project_id=project_id,
            personal=personal,
        )
        hits = results.get("results", [])
        if hits:
            page_id = hits[0].get("id")
            if page_id:
                page = await _get(f"/v1/knowledge/wiki/pages/{page_id}")
                if _page_matches_scope(
                    page,
                    project_id=scoped_project_id,
                ):
                    return page
    except Exception:
        pass

    raise ValueError(f"Page not found: {ref}")


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


@mcp.tool()
async def research(
    query: str,
    personal_only: bool = False,
    project: str = "",
    project_id: str = "",
) -> str:
    """Ask a question and get a researched answer with citations from the
    organization's knowledge base, documents, and connected services.

    This runs an agentic researcher that searches the wiki, uploaded documents,
    and connected services (Slack, Drive, Gmail, Calendar, Jira, etc.) to
    produce a grounded, cited answer. Use this as the primary tool for
    answering questions about the organization.

    Use this for questions like:
    - "What did we decide about X?"
    - "Who is working on Y?"
    - "What's the status of project Z?"
    - "What's on my calendar this week?"
    - "Find everything related to topic W"

    Args:
        query: The question to answer.
        personal_only: If true, only search the user's personal project.
        project_id: Optional project ID to scope the search.
    """
    params = await _scope_params(project=project, project_id=project_id, personal=personal_only)
    body = {"query": query, **params}
    data = await _post("/v1/knowledge/research", body)

    # Format the response: replace {{key}} tokens with [N] numbered refs
    import re

    answer = data.get("answer", "")
    sources = data.get("sources", {})

    # Collect referenced keys in order and replace tokens with [N]
    seen_keys: list[str] = []
    def _replace_token(m):
        key = m.group(1)
        if key not in seen_keys:
            seen_keys.append(key)
        return f"[{seen_keys.index(key) + 1}]"

    formatted_answer = re.sub(r"\{\{([^}]+)\}\}", _replace_token, answer)

    lines = []
    if formatted_answer:
        lines.append(formatted_answer)

    # Sources legend
    if seen_keys:
        lines.append("\n---\nSources:")
        for i, key in enumerate(seen_keys, 1):
            src = sources.get(key, {})
            source_type = src.get("type", key.split(":")[0] if ":" in key else "unknown")
            title = src.get("title", "")
            url = src.get("web_view_url", "")
            line = f"  [{i}] ({source_type}) {title}"
            if url:
                line += f"\n      {url}"
            lines.append(line)

    confidence = data.get("confidence", "")
    gaps = data.get("gaps", "")
    meta = []
    if confidence:
        meta.append(f"Confidence: {confidence}")
    if gaps:
        meta.append(f"Gaps: {gaps}")
    if meta:
        lines.append(f"\n{' | '.join(meta)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Proposal-based writes
# ---------------------------------------------------------------------------


@mcp.tool()
async def kb_propose_create(
    title: str,
    content: str,
    project_id: str = "",
    personal: bool = False,
    page_type: str = "topic",
    summary: str = "",
    parent: str = "",
    sections: list[dict] | None = None,
) -> str:
    """Propose creating a wiki page. Requires exactly one project_id or personal=true.

    This does not write immediately. The proposal must be accepted separately
    with kb_accept_proposal after the user explicitly asks to apply it.

    Args:
        sections: Optional section metadata and citations. Include event dates,
            source citations, and keys matching inline tokens like
            {{source_type:source_id}} in content. Section IDs must match
            <!-- sec:ID --> markers.
    """
    section_data = sections or []
    _validate_sections(content, section_data, require_content=True)
    body = {
        "action": "create",
        "title": title,
        "content": content,
        "page_type": page_type,
        "summary": summary,
        "parent": parent or None,
        "sections": section_data,
        **(await _proposal_scope(project_id, personal)),
    }
    data = await _post("/v1/knowledge/wiki/proposals", body)
    return _format_proposal(data)


@mcp.tool()
async def kb_propose_edit(
    page: str,
    content: str,
    project_id: str = "",
    personal: bool = False,
    title: str = "",
    summary: str = "",
    sections: list[dict] | None = None,
) -> str:
    """Propose replacing a wiki page's content. Requires exactly one project_id or personal=true.

    This does not write immediately. Use kb_accept_proposal only after the user
    explicitly asks to accept/apply the proposal.

    Args:
        sections: Optional section metadata and citations. Include event dates,
            source citations, and keys matching inline tokens like
            {{source_type:source_id}} in the replacement content. Section IDs
            must match <!-- sec:ID --> markers.
    """
    section_data = sections or []
    _validate_sections(content, section_data, require_content=True)
    body = {
        "action": "edit",
        "page": page,
        "content": content,
        "title": title or None,
        "summary": summary,
        "sections": section_data,
        **(await _proposal_scope(project_id, personal)),
    }
    data = await _post("/v1/knowledge/wiki/proposals", body)
    return _format_proposal(data)


@mcp.tool()
async def kb_propose_patch(
    page: str,
    patches: list[dict],
    project_id: str = "",
    personal: bool = False,
    title: str = "",
    summary: str = "",
    sections: list[dict] | None = None,
) -> str:
    """Propose patching a wiki page. Requires exactly one project_id or personal=true.

    Patch ops: replace_text, replace_section, append_section, delete_section,
    insert_after. This does not write immediately.

    Args:
        sections: Optional section metadata and citations. Include event dates,
            source citations, and keys matching inline tokens like
            {{source_type:source_id}} in the proposed content. Section IDs must
            match <!-- sec:ID --> markers after patch application.
    """
    section_data = sections or []
    _validate_sections(None, section_data, require_content=False)
    body = {
        "action": "edit",
        "page": page,
        "patches": patches,
        "title": title or None,
        "summary": summary,
        "sections": section_data,
        **(await _proposal_scope(project_id, personal)),
    }
    data = await _post("/v1/knowledge/wiki/proposals", body)
    return _format_proposal(data)


@mcp.tool()
async def kb_propose_find_replace(
    find: str,
    replace: str,
    project_id: str = "",
    personal: bool = False,
    regex: bool = False,
    summary: str = "",
) -> str:
    """Propose find/replace across pages in one project or the user's personal project."""
    body = {
        "action": "find_replace",
        "replacements": [{"find": find, "replace": replace, "regex": regex}],
        "summary": summary,
        **(await _proposal_scope(project_id, personal)),
    }
    data = await _post("/v1/knowledge/wiki/proposals", body)
    return _format_proposal(data)


@mcp.tool()
async def kb_propose_move(
    page: str,
    project_id: str = "",
    personal: bool = False,
    title: str = "",
    parent: str = "",
    summary: str = "",
) -> str:
    """Propose moving or renaming a wiki page in one project or the user's personal project."""
    body = {
        "action": "mv",
        "page": page,
        "title": title or None,
        "parent": parent or None,
        "summary": summary,
        **(await _proposal_scope(project_id, personal)),
    }
    data = await _post("/v1/knowledge/wiki/proposals", body)
    return _format_proposal(data)


@mcp.tool()
async def kb_propose_archive(
    page: str,
    project_id: str = "",
    personal: bool = False,
    include_children: bool = True,
    summary: str = "",
) -> str:
    """Propose archiving a wiki page in one project or the user's personal project."""
    body = {
        "action": "archive",
        "page": page,
        "include_children": include_children,
        "summary": summary,
        **(await _proposal_scope(project_id, personal)),
    }
    data = await _post("/v1/knowledge/wiki/proposals", body)
    return _format_proposal(data)


@mcp.tool()
async def kb_list_proposals(
    status: str = "pending",
    project_id: str = "",
    personal: bool = False,
    limit: int = 20,
) -> str:
    """List visible wiki proposals, optionally filtered by project_id or personal=true."""
    params: dict = {
        "status": status,
        "limit": min(limit, 100),
        **(await _proposal_filter_scope(project_id, personal)),
    }
    data = await _get("/v1/knowledge/wiki/proposals", params)
    proposals = data.get("proposals", [])
    if not proposals:
        return "No proposals found."
    return "\n\n".join(_format_proposal(p) for p in proposals)


@mcp.tool()
async def kb_show_proposal(proposal_id: str) -> str:
    """Show one wiki proposal before accepting or dismissing it."""
    data = await _get(f"/v1/knowledge/wiki/proposals/{proposal_id}")
    return _format_proposal(data) + "\n\nPayload:\n" + json.dumps(data.get("payload", {}), indent=2)


@mcp.tool()
async def kb_accept_proposal(proposal_id: str) -> str:
    """Accept and apply a wiki proposal.

    Only call this after the user explicitly asks to accept/apply this specific
    proposal. Do not create and accept a proposal in the same assistant step
    unless the user already explicitly requested that behavior.
    """
    data = await _post(f"/v1/knowledge/wiki/proposals/{proposal_id}/accept")
    return f"Accepted proposal {data.get('proposal_id', proposal_id)}."


@mcp.tool()
async def kb_dismiss_proposal(proposal_id: str) -> str:
    """Dismiss a wiki proposal after the user asks to reject it."""
    data = await _post(f"/v1/knowledge/wiki/proposals/{proposal_id}/dismiss")
    return f"Dismissed proposal {data.get('proposal_id', proposal_id)}."


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_server() -> None:
    """Start the MCP server on stdio."""
    mcp.run(transport="stdio")
