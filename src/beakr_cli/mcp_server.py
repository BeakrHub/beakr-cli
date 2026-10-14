"""MCP server exposing Beakr knowledge base tools for Claude Code and other AI assistants."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import PurePosixPath
from typing import Any

from beakr_sdk.sources import public_source as _shared_public_source
from mcp.types import ToolAnnotations

from beakr_cli import tool_surface

# Underscored: every public coroutine in this module is read as an MCP tool.
from beakr_cli.mcp_errors import call_api as _call_api
from beakr_cli.scope import scope_params
from beakr_cli.updates import (
    detect_install,
    get_update_status,
    installed_version_on_path,
    run_upgrade,
)

_INSTRUCTIONS = (
    "Beakr is the team's organizational memory: decisions, processes, ownership and "
    "dated events, so 'why did we do X' is one search away.\n\n"
    "Answer questions about the user's org with citations. Start with knowledge_base "
    "command='sections' to find relevant cited sections. Check the excerpt's fidelity "
    "before quoting it, and open the source when a deciding detail is missing. For "
    "'every X', counts and superlatives use 'ls' (by page_type) or 'rows' to compare "
    "cards, then inspect coverage: compilation, scan and result limits can omit records. "
    "An absent row or graph link does not establish absence from the source documents. "
    "When 'next' appears, follow it; a card scan cap requires narrower searches, "
    "whereas row offset only pages rows already found. Exact wording: "
    "command='sources' {page} lists every cited passage. Each Sources line names where a "
    "document lives and where each cited passage sits (path#p. 7): read_file with that "
    "path#place, a {{artifact:...}} token or a file id opens the original there. Check a "
    "decisive value in the file; when the file and a card disagree, the file wins. In a "
    "code cell (Codex), print "
    "only the result's text and make one Knowledge call per cell. Where 'research' is "
    "available it gives one cited answer across the wiki, documents and connected "
    "services. 'context' once at the start of a task for the user's profiles, memories "
    "and skills; 'skills' to read a SKILL.md before following it.\n\n"
    "Scope: pass 'scope' (a project name or id) to work in one project, or omit it to read "
    "everything you can see; list_projects names them, the user's personal project "
    "included (project_type='personal').\n\n"
    "Writes: when the conversation produces something durable (a decision with its "
    "rationale, a process, an owner, a dated event), check with knowledge_base "
    "command='sections' that it is not already captured, then offer to propose it. "
    "knowledge_base_write only stages a PROPOSAL: call accept_proposal only when the user "
    "asks to accept that proposal. If the result says auto-approve is on, Beakr applies it "
    "itself: do not ask for approval or call accept_proposal. Cite this chat as a "
    "'conversation' source with meta.excerpt. Put a {{key}} citation token after each "
    "factual claim in a section body and list the same sources in sections[].citations "
    "with a stance; reuse a read's source_ref there and never invent Beakr's internal "
    "ids.\n\n"
    "Evidence: an excerpt marked (paraphrase) is the compiler's wording, (human asserted) "
    "was supplied by a person, and (unverified) was never checked against the source -- "
    "open the source before quoting one or resting a conclusion on it. 'at:' locates the "
    "excerpt; 'source changed since cited' means a newer version exists.\n\n"
    "Version: beakr_version says whether this server is out of date; call update_beakr "
    "only when the user asks, then they restart the client."
)


def _update_notice() -> str:
    """One line for the instructions when the cached check says we are behind.

    Cache only: no network while the client is waiting for ``initialize``. The
    background refresh in ``run_server`` updates the cache for the next launch.
    """
    try:
        status = get_update_status(allow_network=False)
    except Exception:
        return ""
    if not status.update_available:
        return ""
    return (
        f"\n\nUPDATE AVAILABLE: this Beakr MCP server is {status.current}; "
        f"{status.latest} is released. Tell the user once, and offer to run update_beakr."
    )


# W8.2: the eager tools are listed by default and the rest are found through
# discover_tools (BEAKR_MCP_TOOLS=all lists everything).
mcp = tool_surface.TieredFastMCP(
    "beakr", instructions=_INSTRUCTIONS + tool_surface.DISCOVERY_NOTE + _update_notice()
)


def _scoped_project(project: str | None = None, project_id: str | None = None) -> str | None:
    """The project filter the non-vocabulary routes still take (argument, else the binding).

    There is no ``personal`` argument, deliberately. Personal is not a special
    scope -- it is a project with ``project_type='personal'`` that
    ``list_projects`` already reports -- so it is named like any other and needs
    no resolution step of its own. The flag used to cost a ``/v1/projects`` round
    trip and a module-level cache to hide it.
    """
    return scope_params(project=project_id or project).get("project_id")


async def _project_by_name_or_id(
    project: str | None = None, project_id: str | None = None
) -> str | None:
    """``_scoped_project`` for a ``project`` that may be a name, as list_projects suggests.

    The research, stats and graph routes take a project id; an agent passes the
    name it read from list_projects (or the ``scope`` it gives knowledge_base) and
    was refused with "project_id must be a valid UUID".
    """
    from beakr_cli.mcp_tools_platform import _project_id

    given = (project_id or project or "").strip()
    if not given:
        return _scoped_project()
    resolved = await _project_id(given)
    if resolved is None:
        raise ValueError(
            f"No single project named {given!r}; pass its id or exact name from list_projects."
        )
    return resolved


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
# Source rendering
#
# The engine's source endpoints return the full citation record -- excerpt,
# fidelity, locator, url, doi, artifact_id, availability, and whether the source
# has changed since it was cited. Every tool below used to receive all of that
# and print two fields of it, so an MCP agent holding a citation could not tell a
# verbatim quote from the compiler's paraphrase, could not see where in the
# document a claim came from, and could not open the source at all.
#
# The in-product twin of this is ``_source_detail_lines`` in
# ``beakr/engines/tools/graph/wiki_tools.py``. This is deliberately NOT a copy of
# it: that surface can mint a per-caller connector handle because it runs inside
# the tool runtime with the caller's ctx, and this one cannot. The shared part is
# the contract -- what a citation is obliged to show a reader -- and it is
# restated here rather than imported because the two repositories do not share a
# package.
# ---------------------------------------------------------------------------

#: Fidelity values meaning the excerpt is the SOURCE's words rather than the
#: compiler's. Mirrors ``QUOTED_FIDELITIES`` in the engine's evidence DTOs.
_QUOTED_FIDELITIES = frozenset({"verbatim", "normalized"})

#: Evidence budget in a multi-result listing. This keeps enough surrounding
#: source text to judge the citation; page/provenance reads return the complete
#: retained 1,000-character context window.
_LIST_EXCERPT_CHARS = 600


def _excerpt_line(source: dict, *, excerpt_chars: int | None) -> str | None:
    """The recorded evidence, with the verdict it was recorded under.

    The verdict rides on the SAME line as the excerpt and never on one below it.
    An excerpt whose fidelity is dropped renders as trusted downstream -- absent
    fidelity is the legacy-trusted case in the frontend's gate -- so it must not
    be possible to read the quote while skipping the label.
    """
    excerpt = str(source.get("excerpt") or (source.get("meta") or {}).get("quote") or "").strip()
    if not excerpt:
        return None
    excerpt = " ".join(excerpt.split())
    if excerpt_chars is not None and len(excerpt) > excerpt_chars:
        excerpt = excerpt[:excerpt_chars].rstrip() + "..."
    fidelity = str(source.get("fidelity") or "")
    if not fidelity:
        # No verdict recorded is NOT the same as verified, and the default
        # downstream treatment is to trust it, so it is called out here.
        verdict = " (unverified)"
    elif fidelity in _QUOTED_FIDELITIES:
        verdict = "" if fidelity == "verbatim" else " (normalized)"
    elif fidelity == "human_asserted":
        verdict = " (human asserted)"
    else:
        verdict = " (paraphrase)"
    return f'"{excerpt}"{verdict}'


def _context_line(source: dict, *, excerpt_chars: int | None) -> str | None:
    """The larger verified source window surrounding the exact quote."""
    evidence = source.get("evidence") or {}
    fidelity = source.get("fidelity") or evidence.get("fidelity")
    if fidelity not in _QUOTED_FIDELITIES:
        return None
    context = str(
        source.get("context_excerpt")
        or evidence.get("context_excerpt")
        or (source.get("meta") or {}).get("context_excerpt")
        or ""
    ).strip()
    if not context:
        return None
    context = " ".join(context.split())
    if excerpt_chars is not None and len(context) > excerpt_chars:
        context = context[:excerpt_chars].rstrip() + "..."
    return f"context: {context}"


def _source_lines(source: dict, *, indent: str, excerpt_chars: int | None) -> list[str]:
    """The evidence and access lines under one source.

    Used by ``research``; every knowledge_base command arrives already rendered by
    the engine, which owns one formatter shared with the in-product tools, so
    the four cannot describe the same citation differently -- they previously
    did, each printing its own two-field subset.
    """
    lines: list[str] = []
    meta = source.get("meta") or {}

    provider = source.get("source_provider") or meta.get("provider")
    path = source.get("display_path") or source.get("path") or meta.get("path")
    filename = source.get("filename") or source.get("file_name") or meta.get("filename")
    if not filename and path:
        filename = PurePosixPath(str(path).rstrip("/")).name
    if filename or provider:
        identity = " · ".join(str(value) for value in (filename, provider) if value)
        lines.append(f"{indent}{identity}")

    bits: list[str] = []
    if source.get("unit_kind"):
        bits.append(str(source["unit_kind"]))
    availability = str(source.get("source_availability") or "")
    if availability:
        bits.append(availability)
    if source.get("source_moved"):
        # Not the same fact as a stale anchor: the source has a newer reading
        # than the one this claim was checked against.
        bits.append("source changed since cited")
    if bits:
        lines.append(f"{indent}{' · '.join(bits)}")

    excerpt = _excerpt_line(source, excerpt_chars=excerpt_chars)
    if excerpt:
        lines.append(f"{indent}{excerpt}")
    context = _context_line(source, excerpt_chars=excerpt_chars)
    if context:
        lines.append(f"{indent}{context}")

    if source.get("locator"):
        lines.append(f"{indent}at: {source['locator']}")

    url = source.get("url") or source.get("web_view_url") or meta.get("web_view_url")
    if url:
        lines.append(f"{indent}url: {url}")
    if path:
        lines.append(f"{indent}path: {path}")
    if source.get("doi"):
        lines.append(f"{indent}doi: {source['doi']}")

    # No connector handle or artifact id here. They are Beakr implementation
    # details and this external surface has no tool that can redeem them. The
    # structured result carries an opaque source_ref plus public URL/path.
    if availability and availability != "live":
        lines.append(f"{indent}unavailable: source is {availability}")

    return lines


def _result_text(result: dict) -> str:
    """Extract the canonical human-readable rendering from an engine result."""
    blocks = result.get("content") if isinstance(result, dict) else None
    if isinstance(blocks, list):
        text_blocks = [
            str(block["text"])
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        if text_blocks:
            return "\n".join(text_blocks)
    return json.dumps(result, indent=2, default=str)


def _public_source(source: dict, *, citation_key: str | None = None) -> dict:
    """Keep only the public source contract; never forward backend row fields.

    The one shape the SDK, the CLI and this server share (``beakr_sdk.sources``).
    """
    return _shared_public_source(source, citation_key=citation_key)


def _wiki_result(result: dict) -> dict:
    """Return canonical text plus the engine's sanitized structured sources."""
    outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else {}
    return {
        "content": _result_text(result),
        "sources": [
            _public_source(source)
            for source in result.get("sources", [])
            if isinstance(source, dict)
        ],
        "status": outcome.get("status", "ok"),
        **({"error_code": outcome["error_code"]} if outcome.get("error_code") else {}),
        # A read's coverage and next-step hints, as the engine sent them
        # (beakr/engines/tools/graph/kb_result_coverage.py in beakr-engine).
        **({"coverage": result["coverage"]} if isinstance(result.get("coverage"), dict) else {}),
        # Older compact API responses report truncation only through next.
        "next": list(result.get("next") or []),
    }


#: A span token's passage suffix: ``artifact:<version>-u3`` is passage 3 of the
#: document ``artifact:<version>``.
_SPAN_SUFFIX = re.compile(r"-u\d+$")


def _research_document(key: str, source: dict) -> str:
    """What makes two cited passages the same document in a research answer."""
    meta = source.get("meta") or {}
    for name in ("artifact_id", "file_id", "artifact_version_id"):
        value = source.get(name) or meta.get(name) or (source.get("location") or {}).get(name)
        if value:
            return f"{name}:{value}"
    return _SPAN_SUFFIX.sub("", key)


#: Appended wherever sources are listed. Says what the marks mean rather than
#: leaving a reader to infer that an unmarked quote and a "(paraphrase)" one
#: carry the same weight.
_SOURCE_NOTE = (
    "\nCite with the {{token}}. An excerpt marked (paraphrase) is the compiler's "
    "wording, one marked (human asserted) was supplied by a person, and one marked "
    "(unverified) was never checked against the source -- "
    "open the url before quoting either, or before resting a conclusion on it."
)


# ---------------------------------------------------------------------------
# Workspace discovery
# ---------------------------------------------------------------------------


@mcp.tool(**tool_surface.tool_kwargs("list_projects"))
async def list_projects(cursor: str = "", limit: int = 50) -> dict[str, Any]:
    """List projects in the organization, including the user's personal project.

    Pass a project's name or id as ``scope`` to knowledge_base and
    knowledge_base_write, or as ``project`` to research, wiki_stats and wiki_graph.
    One page at a time (SPEC-488); the hosted server answers the same shape.

    Args:
        cursor: The next_cursor from the previous page's coverage.
        limit: Projects per page (1-50).
    """
    from beakr_cli.mcp_tools_platform import _cursor, _page_of, _page_result, _page_size

    page = await _call_api(
        lambda sdk: sdk.api.projects.list_projects(cursor=_cursor(cursor), limit=_page_size(limit))
    )
    rows, has_more, next_cursor = _page_of(page, "data", "projects")
    lines = []
    for p in rows[:50]:
        name = p.get("name", "Untitled")
        pid = p.get("id", "")
        project_type = p.get("project_type", "standard")
        desc = p.get("description", "") or ""
        lines.append(f"- {name} ({project_type}, id: {pid}){f' -- {desc}' if desc else ''}")
    return _page_result(
        "\n".join(lines) if lines else "No projects found.",
        tool="list_projects",
        returned=min(len(rows), 50),
        has_more=has_more,
        next_cursor=next_cursor,
    )


@mcp.tool(**tool_surface.tool_kwargs("get_profile"))
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
        data = await _call_api(lambda sdk: sdk.profile.org())
    elif profile_type == "project" and project_id:
        data = await _call_api(lambda sdk: sdk.profile.project(project_id))
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


@mcp.tool(**tool_surface.tool_kwargs("knowledge_base"))
async def knowledge_base(
    command: str,
    arguments: dict | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Read the knowledge base: the same commands Beakr's in-product agents use.

    Pass the command name as ``command`` and its options as a flat ``arguments``
    object. There is no ``search`` command: use ``sections`` or ``grep``.

    Find pages:
      sections   {query, page_type?, limit?}  Semantic section search. START HERE.
      grep       {query, mode?, context_lines?, matches_per_page?, limit?}
                 mode: text (default, literal substring), regex, semantic, history.
      ls         {parent?, page_type?, sort_by?, limit?}  sort_by: title|updated_at|created_at.
    Read:
      cat        {page, section_id?, lines?, outline?, rev?}  Prefer section_id from a hit.
      hover      {page}  Type, summary, revision, counts. Cheap triage before cat.
    Graph:
      links      {page}  Backlinks.     references {page}  Links plus prose mentions.
      timeline   {timeline_query?, start_date?, end_date?, include_content?, include_approx?}
      rows       {table: facts|connections|findings, page_type?, entity?, attribute?, query?,
                  start_date?, end_date?, current_only?, order_by?, descending?, limit?, offset?,
                  include_evidence?}
                 Every map card's rows as one table, each tagged with its card (entity).
                 include_evidence adds each row's cited passage (at most 50 rows).
                 current_only keeps the latest When per attribute; a connection's `to` is
                 an entity, so facts and connections join on it.
      ontology   {include_proposed?}   suggest_parent {page | title, content?}
    Trust and lineage:
      provenance {page}  Per-section citations with stance.
      blame      {page}  Paragraph-level attribution.
      sources    {page}  Sources behind a page (or pass a source/citation token as page).
    History:
      log {page?}   diff {page, rev, rev2?}   show {page, rev}
    Utility:
      diagnostics {}   completions {prefix}   proposals {status?}  status: pending|dismissed.

    ``page`` accepts a slug, title, UUID, or wiki citation key such as ``wiki:8f3a0c21``.
    Results carry structured ``sources``, each with an opaque ``source_ref`` to reuse
    in knowledge_base_write citations.

    Args:
        command: One of the commands above.
        arguments: That command's options, e.g. {"query": "billing owner"}.
        scope: Optional project name or ID. Omit to read everything you can see.
    """
    # check=False: a refusal the door reports in ``outcome`` is shown to the agent.
    return _wiki_result(
        await _call_api(
            lambda sdk: sdk.knowledge.relay_command(command, arguments, scope=scope, check=False)
        )
    )


@mcp.tool(**tool_surface.tool_kwargs("wiki_stats"))
async def wiki_stats(
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Show knowledge base statistics (page count, source count).

    Args:
        project: Project name or ID to scope the stats. Omit for everything you can see.
        project_id: Deprecated alias for project.
    """
    scoped = await _project_by_name_or_id(project, project_id)
    data = await _call_api(lambda sdk: sdk.knowledge.pages.stats(project_id=scoped))
    return f"Pages: {data.get('pages', 0)}\nSources: {data.get('sources', 0)}"


@mcp.tool(**tool_surface.tool_kwargs("wiki_graph"))
async def wiki_graph(
    project: str | None = None,
    project_id: str = "",
) -> dict[str, Any]:
    """Get a summary of the knowledge base graph structure.

    Returns the top connected pages and basic graph stats. Use knowledge_base command='links'
    on a specific page to explore its neighborhood.

    Args:
        project: Project ID to scope the graph. Omit for everything you can see.
        project_id: Deprecated alias for project.
    """
    from beakr_cli.mcp_tools_platform import _capped_result

    scoped = await _project_by_name_or_id(project, project_id)
    data = await _call_api(
        lambda sdk: sdk.knowledge.pages.graph(project_id=scoped, max_nodes=_GRAPH_MAX_NODES)
    )
    data = data if isinstance(data, dict) else {}
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Count connections per node
    connections: dict[str, int] = {}
    for e in edges:
        for end in (e.get("from"), e.get("to")):
            if end:
                connections[end] = connections.get(end, 0) + 1

    # Build summary
    lines = [f"Graph: {len(nodes)} pages, {len(edges)} links\n"]
    lines.append("Top connected pages:")
    node_map = {n.get("id"): n for n in nodes}
    ranked = sorted(connections.items(), key=lambda x: x[1], reverse=True)[:15]
    for node_id, count in ranked:
        n = node_map.get(node_id, {})
        label = n.get("label", str(node_id)[:8])
        ptype = n.get("page_type", "")
        lines.append(f"  {label} ({ptype}) - {count} links")

    return _capped_result(
        "\n".join(lines),
        tool="wiki_graph",
        returned=len(nodes),
        total=None,
        complete=len(nodes) < _GRAPH_MAX_NODES,
        hint=(
            f"The graph summary is capped at {_GRAPH_MAX_NODES} pages: pass project to narrow "
            "it, or use knowledge_base command='links' on one page."
        ),
    )


#: Pages the graph summary asks for; a graph that returns this many may have more.
_GRAPH_MAX_NODES = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


@mcp.tool(**tool_surface.tool_kwargs("documents"))
async def documents(
    command: str, arguments: dict[str, Any] | None = None, project: str | None = None
) -> dict:
    """Search original indexed text or read a document window, within a project."""
    scoped = await _project_by_name_or_id(project)
    data = await _call_api(
        lambda sdk: sdk.knowledge.documents(command, project_id=scoped, **(arguments or {}))
    )
    return {
        "content": data.get("content", ""),
        "sources": [
            _public_source(source, citation_key=key)
            for key, source in (data.get("sources") or {}).items()
        ],
        "status": "error" if data.get("is_error") else "ok",
    }


@mcp.tool(**tool_surface.tool_kwargs("research"))
async def research(
    query: str,
    project: str = "",
    project_id: str = "",
) -> dict:
    """Ask a question and get a researched answer with citations from the
    organization's knowledge base, documents, and connected services.

    This runs an agentic researcher that searches the wiki, uploaded documents,
    and connected services (Slack, Drive, Gmail, Calendar, Jira, etc.) to
    produce a grounded, cited answer. Use this as the primary tool for
    answering questions about the organization.

    Preserve the user's question and scope when delegating: pass their original
    wording, adding context only when needed. Do not silently restrict a broad
    concept to an exact taxonomy label. For counts, averages, all/none claims,
    inspect checked/not_checked coverage: citations support the listed evidence
    but do not establish that every qualifying record was found. Resolve material
    population gaps before presenting a result as exhaustive.

    Use this for questions like:
    - "What did we decide about X?"
    - "Who is working on Y?"
    - "What's the status of project Z?"
    - "What's on my calendar this week?"
    - "Find everything related to topic W"

    Args:
        query: The question to answer.
        project: Optional project ID to scope the search.
        project_id: Deprecated alias for project.
    """
    from beakr_cli.research_metadata import (
        attach_research_metadata,
        record_failed_research,
        with_cited_labels,
    )

    scoped = await _project_by_name_or_id(project, project_id)
    started = time.monotonic()
    try:
        data = await _call_api(lambda sdk: sdk.knowledge.research(query, project_id=scoped))
    except (Exception, asyncio.CancelledError) as exc:
        record_failed_research(exc, time.monotonic() - started)
        raise

    data = with_cited_labels(data)

    citations = data.get("citations")
    if isinstance(citations, list) and isinstance(data.get("text"), str):
        return attach_research_metadata(_research_from_citations(data, citations), data)

    # An engine without the public answer form (no `text` / `citations`).
    # Format the response: replace {{key}} tokens with [N] numbered refs, one
    # number per cited DOCUMENT (the hosted server's formatting.research): two
    # passages of the same file share it, so the list never shows one file
    # twice (proof run 2026-10-13 listed it as [1] and [2]).
    answer = data.get("answer", "")
    sources = data.get("sources", {}) if isinstance(data.get("sources"), dict) else {}
    documents: list[str] = []
    tokens: dict[str, list[str]] = {}
    seen_keys: list[str] = []

    def _replace_token(m):
        key = m.group(1)
        src = sources.get(key) if isinstance(sources.get(key), dict) else {}
        document = _research_document(key, src)
        if document not in tokens:
            documents.append(document)
            tokens[document] = []
        if key not in tokens[document]:
            tokens[document].append(key)
        if key not in seen_keys:
            seen_keys.append(key)
        return f"[{documents.index(document) + 1}]"

    formatted_answer = re.sub(r"\{\{([^}]+)\}\}", _replace_token, answer)

    lines = []
    if formatted_answer:
        lines.append(formatted_answer)

    # Sources legend
    if documents:
        lines.append("\n---\nSources:")
        for index, document in enumerate(documents, 1):
            keys = tokens[document]
            src = sources.get(keys[0], {}) if isinstance(sources.get(keys[0]), dict) else {}
            source_type = src.get("type", keys[0].split(":")[0] if ":" in keys[0] else "unknown")
            # The tokens as well as the ordinal. [1] is readable prose but names
            # nothing outside this one answer, and the instructions above tell a
            # writer to reuse these keys in sections[].citations -- so the keys
            # have to survive the rendering that makes the answer readable.
            shown = " ".join(f"{{{{{key}}}}}" for key in keys)
            lines.append(f"  [{index}] {shown}  ({source_type}) {src.get('title', '')}")
            # Excerpt and verdict, on the primary tool. This is where an MCP
            # consumer is most exposed: research answers read as authoritative
            # prose, and without the verdict a compiler paraphrase is
            # indistinguishable from a checked quote.
            lines.extend(_source_lines(src, indent="      ", excerpt_chars=_LIST_EXCERPT_CHARS))
        lines.append(_SOURCE_NOTE)

    confidence = data.get("confidence", "")
    gaps = data.get("gaps", "")
    meta = []
    if confidence:
        meta.append(f"Confidence: {confidence}")
    if gaps:
        meta.append(f"Gaps: {gaps}")
    if meta:
        lines.append(f"\n{' | '.join(meta)}")

    return attach_research_metadata(
        {
            "content": "\n".join(lines),
            # Only the sources the answer cites, in citation order: the researcher
            # returns everything it read (69 records, ~16k tokens, for a two-citation
            # answer on the dogfood instance), which the [n] legend never shows.
            "sources": [
                _public_source(sources[key], citation_key=key)
                for key in seen_keys
                if isinstance(sources.get(key), dict)
            ],
            "confidence": confidence or None,
            "gaps": gaps or None,
        },
        data,
    )


def _numbered_sources(citations: list[dict]) -> list[str]:
    """``Sources:`` then ``[n]`` and one entry per cited document, within ``SOURCES_CHARS``.

    The hosted server's ``formatting._numbered_sources``: the same Sources list
    a Knowledge read shows, numbered as the answer's ``[n]`` and capped alike.
    """
    from beakr_cli import mcp_render

    lines = ["\n---\nSources:"]
    used = 0
    for number, record in enumerate(citations, 1):
        block = mcp_render.sources_block([record]).splitlines()[1:]
        if not block:
            continue
        block[0] = f"[{number}] " + block[0].removeprefix("- ")
        size = sum(len(line) + 1 for line in block)
        if number > 1 and used + size > mcp_render.SOURCES_CHARS:
            lines.append(
                f"+{len(citations) - number + 1} more sources: knowledge_base command='sources' "
                "with a citation token reads one."
            )
            break
        lines.extend(block)
        used += size
    return lines


def _research_from_citations(data: dict, citations: list) -> dict:
    """A research answer in the engine's public form, rendered as the hosted server does.

    The engine already numbers each cited document once in ``text`` and keeps
    one record per document in ``citations`` (brief excerpts); ``next`` says
    how to read what it trimmed. Byte for byte the hosted
    ``formatting.research`` (tests/test_research_rendering.py).
    """
    lines = [data["text"]] if data["text"] else []
    if citations:
        lines.extend(_numbered_sources(citations))
        lines.append(_SOURCE_NOTE)
    meta = [
        f"{label}: {data[k]}"
        for k, label in (("confidence", "Confidence"), ("gaps", "Gaps"))
        if data.get(k)
    ]
    if meta:
        lines.append("\n" + " | ".join(meta))
    return {
        "content": "\n".join(lines),
        "sources": [_public_source(record) for record in citations],
        "next": list(data.get("next") or []),
        "confidence": data.get("confidence") or None,
        "gaps": data.get("gaps") or None,
    }


# ---------------------------------------------------------------------------
# Proposal-based writes
# ---------------------------------------------------------------------------


@mcp.tool(**tool_surface.tool_kwargs("knowledge_base_write"))
async def knowledge_base_write(
    action: str,
    arguments: dict | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Propose a knowledge-base change. Nothing is written until accept_proposal --
    unless the result says the user's auto-approve is on, in which case Beakr
    applies it automatically (new, edit and edit_section only).

    Pass the action as ``action`` and its fields as a flat ``arguments`` object.
    Every change except page_type needs ``rationale``; edits also take ``edit_note``.

      edit_section {page, section_id, new_section_body, citations?, section_meta?,
                    section_title?, after?}  PREFERRED for updating a page. Replaces one
                    section's whole body; a new section_id plus section_title adds one.
      add_fact     {page, attribute, value, evidence, when?, citations?}
                    One Facts row on a project-map card, merged into the live table.
                    attribute is named stably; value is the statement as the source
                    gives it, qualifiers included; when is when it happened or held
                    (YYYY-MM-DD, YYYY-MM, YYYY-Qn or YYYY).
      add_connection {page, relation, to, evidence, when?, citations?}
                    One Connections row; relation is a short verb phrase carrying any
                    detail; `to` is the other page's title.
      add_finding  {page, date, kind, text, evidence, about?, finding_scope?, supersedes?}
                    One dated Findings bullet. kind: result|lesson|decision|question|
                    correction. Your conclusions go here, not in Facts. evidence holds
                    the {{key}} token of each source. Each of the three takes
                    rows=[{...}, ...] to add several at once (items inherit evidence
                    and when). They are refused on a page that is not a card outside
                    a project map: use edit_section there.
      sort_table   {page, table: facts|connections, order_by}  e.g. 'attribute, when desc'.
                    Sorts the table and keeps it sorted on every later write.
      edit_rows    {page, table: facts|connections, old_rows, rows, reason?}  Edits,
                    merges or deletes rows already on a card. old_rows names each row
                    as the card shows it ({attribute, value} or {relation, to}); rows
                    replace them (empty deletes). A new row with no evidence keeps the
                    replaced rows' citations. reason is required to delete rows or to
                    drop a citation. Several at once: edits=[{old_rows, rows, reason}].
      new          {title, page_type, summary, sections, parent?, page_aliases?}
      edit         {page, patches | sections, title?}  patches: [{op, section?, content?,
                    find?, replace?, regex?, after?}]; sections replaces the whole page.
      find_replace {replacements: [{find, replace, regex?}]}  Needs scope.
      mv           {page | pages | moves, parent?, title?}   Same project only.
      archive      {page | pages, include_children?}
      merge        {page (the duplicate), canonical, merge_sections?}  Prefer over archive
                    for duplicates: links keep resolving.
      copy         {page | pages, include_children?, source_scope?}  scope = TARGET project.
      page_type    {key, label, description, when_to_use, ...}  Only if no type fits.

    ``sections`` is an ordered list of {title, body, id?, citations?, event_start?,
    event_end?, date_precision?}. Beakr writes the section markers; do not author
    them. Citation objects are {source_ref | key, stance, meta?}; stance is
    support, qualifies, or contradicts. Use a ``source_ref`` from a knowledge_base
    read, or for this conversation: {key: "conversation:<id>", source_type:
    "conversation", source_title, stance, meta: {excerpt}}.

    Args:
        action: One of the actions above.
        arguments: That action's fields.
        scope: Project name or ID the change belongs to.
    """
    return _wiki_result(
        await _call_api(
            lambda sdk: sdk.knowledge.relay_write(action, arguments, scope=scope, check=False)
        )
    )


@mcp.tool(**tool_surface.tool_kwargs("show_proposal"))
async def show_proposal(proposal_id: str) -> str:
    """Show one wiki proposal before accepting or dismissing it."""
    data = await _call_api(lambda sdk: sdk.knowledge.proposals.show(proposal_id))
    return _format_proposal(data) + "\n\nPayload:\n" + json.dumps(data.get("payload", {}), indent=2)


@mcp.tool(**tool_surface.tool_kwargs("accept_proposal"))
async def accept_proposal(proposal_id: str) -> str:
    """Accept and apply a wiki proposal.

    Only call this after the user explicitly asks to accept/apply this specific
    proposal. Do not create and accept a proposal in the same assistant step
    unless the user already explicitly requested that behavior.
    """
    data = await _call_api(lambda sdk: sdk.knowledge.proposals.accept(proposal_id))
    return f"Accepted proposal {data.get('proposal_id', proposal_id)}."


@mcp.tool(**tool_surface.tool_kwargs("dismiss_proposal"))
async def dismiss_proposal(proposal_id: str) -> str:
    """Dismiss a wiki proposal after the user asks to reject it."""
    data = await _call_api(lambda sdk: sdk.knowledge.proposals.dismiss(proposal_id))
    return f"Dismissed proposal {data.get('proposal_id', proposal_id)}."


# ---------------------------------------------------------------------------
# Version and self-update
# ---------------------------------------------------------------------------


@mcp.tool(
    **tool_surface.tool_kwargs("beakr_version"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
)
async def beakr_version() -> str:
    """Report this Beakr MCP server's version and whether a newer release exists.

    Call when the user asks about the Beakr version or updates, or when a Beakr
    tool behaves as if it is out of date.
    """
    import asyncio
    from datetime import timedelta

    status = await asyncio.to_thread(get_update_status, max_age=timedelta(hours=1))
    info = detect_install()
    lines = [f"Installed: beakr-cli {status.current} ({info.method.value})"]
    if status.latest is None:
        lines.append("Latest release: unknown (PyPI could not be checked).")
    elif status.update_available:
        lines.append(f"Update available: {status.latest}.")
        if info.upgrade_command is not None:
            lines.append("update_beakr can install it; the user must then restart the client.")
        else:
            lines.append(info.instructions)
    else:
        lines.append(f"No newer release (latest release: {status.latest}).")
    return "\n".join(lines)


@mcp.tool(
    **tool_surface.tool_kwargs("update_beakr"),
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def update_beakr() -> str:
    """Upgrade beakr-cli to the latest release and refresh its installed skills.

    Only call this when the user has asked to update Beakr. It takes no arguments
    and only ever upgrades the beakr-cli package with the tool that installed it
    (uv or pipx). The running server keeps the old code: tell the user to restart
    Claude Code / Codex afterwards.
    """
    import asyncio
    from datetime import timedelta

    status = await asyncio.to_thread(get_update_status, max_age=timedelta(0))
    if status.latest is not None and not status.update_available:
        return f"No update needed: installed {status.current}, latest release {status.latest}."
    info = detect_install()
    result = await asyncio.to_thread(run_upgrade, info, expected_version=status.latest)
    if not result.ok:
        tail = f"\n\n{result.output[-2000:]}" if result.output else ""
        return f"Update did not run: {result.message}{tail}"
    now = await asyncio.to_thread(installed_version_on_path)
    return (
        f"{result.message} beakr on PATH is now {now or 'unknown'} "
        f"(this server is still {status.current}). Restart Claude Code / Codex to load it."
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _refresh_update_cache() -> None:
    """Refresh the release cache in the background and note staleness on stderr.

    stderr is the MCP client's log, never the protocol stream. Failures are
    silent: an update check must never take the server down.
    """
    import sys

    try:
        status = get_update_status()
    except Exception:
        return
    if status.update_available:
        print(
            f"beakr-cli {status.latest} is available (running {status.current}). "
            "Run `beakr update`.",
            file=sys.stderr,
        )


def run_server() -> None:
    """Start the MCP server on stdio."""
    import threading

    threading.Thread(target=_refresh_update_cache, name="beakr-update-check", daemon=True).start()
    mcp.run(transport="stdio")


# Platform tools (context, skills, conversations) live in their own module to keep
# this one focused. The names are bound here so the surface tests, which read this
# module's namespace, see the whole tool set.
from beakr_cli import mcp_tools_platform as _platform  # noqa: E402

context = _platform.context
skills = _platform.skills
conversations_search = _platform.conversations_search
import_conversation = _platform.import_conversation
org_audit_log = _platform.org_audit_log
read_file = _platform.read_file
workboard = _platform.workboard
discover_tools = _platform.discover_tools
_platform.register_platform_tools(mcp)
