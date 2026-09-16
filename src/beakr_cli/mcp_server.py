"""MCP server exposing Beakr knowledge base tools for Claude Code and other AI assistants."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from beakr_cli.client import get_async_client, scope_params
from beakr_cli.updates import (
    detect_install,
    get_update_status,
    installed_version_on_path,
    run_upgrade,
)

_INSTRUCTIONS = (
        "Beakr is the team's organizational memory and connected services. "
        "Teams write decisions, processes, ownership, and dated events here so "
        "that 6 months later the answer to 'why did we do X' is one search "
        "away instead of lost in Slack archives or tribal knowledge.\n\n"
        "RESPONSIBILITIES:\n"
        "Use Beakr to answer questions about the user's org with citations. "
        "When the conversation produces something durable (a decision with "
        "rationale, a named process, an ownership assignment, a dated event, "
        "a key relationship), propose adding it -- don't wait to be asked. "
        "First check with knowledge_base command='sections' that it isn't already "
        "captured, then surface a brief suggestion before writing ('this seems worth "
        "capturing -- want me to propose it?'). Never auto-write; always go "
        "through proposals and wait for explicit acceptance. Cite the current "
        "session as a 'conversation' inline source with meta.excerpt when the "
        "information originates from this chat.\n\n"
        "TOOLS:\n"
        "- 'research': Primary tool. Ask any question and get a cited answer from the "
        "wiki, documents, and connected services (Slack, Gmail, Calendar, Jira, etc.). "
        "Use this first for most questions.\n"
        "- 'knowledge_base': every read over the wiki, by command -- ls, cat, grep, "
        "sections, log, blame, diff, show, sources, provenance, links, timeline, "
        "ontology, hover, references, diagnostics, completions, proposals, "
        "suggest_parent. Start with 'sections' or 'grep' to find pages and 'cat' to "
        "read one; 'cat' returns the page's sources and their recorded excerpts with "
        "it. Read results also return structured public source records with an opaque "
        "source_ref, filename, provider, path/URL, exact retained excerpt, verified "
        "surrounding context, and locator. "
        "Reuse source_ref in a later write citation; do not invent or request Beakr's "
        "internal Unit, connector, artifact, or database IDs. This is the same tool, "
        "under the same name with the same commands, that "
        "Beakr's in-product agents use.\n"
        "- 'knowledge_base_write': every change, by action -- new, edit, edit_section, "
        "find_replace, mv, archive, merge, copy, page_type. Every action stages a "
        "PROPOSAL; nothing is applied until accept_proposal.\n\n"
        "SCOPING:\n"
        "Beakr organizes knowledge into projects, including each user's personal "
        "project. Pass 'scope' with a project name or id to work in one project; omit "
        "it to read everything you can see. Use list_projects to discover projects and "
        "their ids -- the personal one is reported with project_type='personal' and is "
        "scoped exactly like any other project.\n\n"
        "WRITES:\n"
        "All wiki writes go through proposals. Stage one with knowledge_base_write, "
        "review it with show_proposal, and only call accept_proposal after "
        "the user explicitly asks to accept/apply that specific proposal. A page's "
        "content is `sections`: an ordered list of {title, body} objects; Beakr writes "
        "the <!-- sec:ID --> markers and ids, so do not author markers. Prefer "
        "action='edit_section' for changes to an existing page. Each section can carry "
        "citations and event dates (full YYYY-MM-DD with date_precision). "
        "Put inline citation tokens like {{source_type:source_id}} or "
        "{{!source_type:source_id}} directly in wiki markdown after every factual "
        "claim, table row/value, date, title, and relationship. Use the same "
        "source keys in sections[].citations with stance so section provenance "
        "can roll up support, qualification, and contradiction. Citations can "
        "reference existing Beakr sources from knowledge_base command='sources' or "
        "command='provenance', "
        "external identifiers, or inline source_type 'conversation', "
        "'agent_note', or 'user_note' with source_title and meta.excerpt/content/text. "
        "Section objects use this shape: "
        "{title, body, id (only to keep an existing section), event_start, event_end, "
        "date_precision, citations:["
        "{source_ref, key, source_type, source_id, source_title, stance, chunk_ref, meta}]}. "
        "For a source returned by Beakr, source_ref plus stance is sufficient and preferred.\n\n"
        "EVIDENCE:\n"
        "Source listings show the filename/location, exact excerpt a claim rests on, "
        "and a larger `context:` window copied from the same source when verification "
        "was possible, plus the verdict the exact excerpt was "
        "recorded under. An excerpt marked (paraphrase) is the compiler's wording "
        "and one marked (human asserted) was supplied by a person without "
        "source-text verification; "
        "and one marked (unverified) was never checked against the source. Do not "
        "copy either into a new citation as if it were a quote, and do not rest a "
        "conclusion on one without opening the source -- an unverified excerpt "
        "propagated into a proposal becomes an unverified claim on a permanent "
        "page. 'at:' gives the location within the document; 'source changed since "
        "cited' means the source has a newer version than the one the claim was "
        "checked against.\n\n"
        "VERSION:\n"
        "beakr_version reports whether this MCP server is out of date. Only call "
        "update_beakr when the user asks to update Beakr; afterwards they must restart "
        "Claude Code / Codex to load the new server."
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


mcp = FastMCP("beakr", instructions=_INSTRUCTIONS + _update_notice())


async def _get(
    path: str,
    params: dict | None = None,
    *,
    project: str | None = None,
    project_id: str | None = None,
) -> dict:
    """GET, with the optional project filter the non-vocabulary routes still take.

    There is no ``personal`` argument, deliberately. Personal is not a special
    scope -- it is a project with ``project_type='personal'`` that
    ``list_projects`` already reports -- so it is named like any other and needs
    no resolution step of its own. The flag used to cost a ``/v1/projects`` round
    trip and a module-level cache to hide it.
    """
    merged = {
        **scope_params(project=project_id or project),
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
    """Keep only the public source contract; never forward backend row fields."""
    meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
    public_location = source.get("location") if isinstance(source.get("location"), dict) else {}
    public_evidence = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
    public_status = source.get("status") if isinstance(source.get("status"), dict) else {}
    public_section = source.get("section") if isinstance(source.get("section"), dict) else {}
    public_retrieval = (
        source.get("retrieval") if isinstance(source.get("retrieval"), dict) else {}
    )

    def first(*keys: str):
        for key in keys:
            value = source.get(key)
            if value not in (None, "", []):
                return value
            value = meta.get(key)
            if value not in (None, "", []):
                return value
        return None

    name = str(first("name", "source_title", "title") or "Untitled source")
    kind = first("source_kind", "unit_kind", "type", "source_type")
    path = public_location.get("display_path") or first("display_path", "path", "local_path")
    filename = first("filename", "file_name")
    if not filename and path:
        filename = PurePosixPath(str(path).rstrip("/")).name
    if not filename and str(kind or "").lower() in {"file", "document", "artifact"}:
        filename = name

    item = {
        "source_ref": first("source_ref"),
        "citation_key": citation_key or first("citation_key", "key"),
        "name": name,
        "filename": filename,
        "provider": first("provider", "source_provider"),
        "source_kind": kind,
    }
    location = {
        "url": public_location.get("url")
        or first("url", "web_view_url", "full_text_url", "pdf_url"),
        "display_path": path,
        "provider_item_id": public_location.get("provider_item_id")
        or first("provider_file_id", "remote_file_id"),
    }
    evidence = {
        "excerpt": public_evidence.get("excerpt") or first("excerpt", "excerpt_text", "quote"),
        "locator": public_evidence.get("locator") or first("locator", "chunk_ref"),
        "fidelity": public_evidence.get("fidelity") or first("fidelity"),
        "evidence_kind": public_evidence.get("evidence_kind") or first("evidence_kind"),
        "anchor_status": public_evidence.get("anchor_status") or first("anchor_status"),
    }
    source_status = {
        "observed_at": public_status.get("observed_at") or first("observed_at"),
        "availability": public_status.get("availability") or first("source_availability"),
        "changed_since_cited": public_status.get("changed_since_cited")
        if "changed_since_cited" in public_status
        else first("source_moved"),
    }
    section = {
        "title": public_section.get("title") or first("section_title"),
        "event_start": public_section.get("event_start") or first("event_start"),
        "event_end": public_section.get("event_end") or first("event_end"),
        "date_precision": public_section.get("date_precision") or first("date_precision"),
    }
    for key, value in (
        ("location", location),
        ("evidence", evidence),
        ("status", source_status),
        ("section", section),
    ):
        cleaned = {k: v for k, v in value.items() if v not in (None, "")}
        if cleaned:
            item[key] = cleaned
    retrieval_tool = public_retrieval.get("tool")
    retrieval_args = public_retrieval.get("arguments")
    if (
        isinstance(retrieval_tool, str)
        and retrieval_tool
        and isinstance(retrieval_args, dict)
        and isinstance(retrieval_args.get("handle"), str)
        and retrieval_args["handle"]
    ):
        item["retrieval"] = {
            "tool": retrieval_tool,
            "arguments": {"handle": retrieval_args["handle"]},
        }
    return {key: value for key, value in item.items() if value not in (None, "")}


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
    }


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


@mcp.tool()
async def list_projects() -> str:
    """List projects in the organization, including the user's personal project.

    Pass a project's name or id as ``scope`` to knowledge_base and
    knowledge_base_write, or as ``project`` to research, wiki_stats and wiki_graph.
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
async def knowledge_base(
    command: str,
    arguments: dict | None = None,
    scope: str | None = None,
) -> dict:
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
    body = dict(arguments or {})
    body["command"] = command
    if scope:
        body["scope"] = scope
    return _wiki_result(await _post("/v1/knowledge/wiki/command", body))


@mcp.tool()
async def wiki_stats(
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Show knowledge base statistics (page count, source count).

    Args:
        project: Project name or ID to scope the stats. Omit for everything you can see.
        project_id: Deprecated alias for project.
    """
    data = await _get(
        "/v1/knowledge/stats",
        project=project,
        project_id=project_id,
    )
    return f"Pages: {data.get('pages', 0)}\nSources: {data.get('sources', 0)}"


@mcp.tool()
async def wiki_graph(
    project: str | None = None,
    project_id: str = "",
) -> str:
    """Get a summary of the knowledge base graph structure.

    Returns the top connected pages and basic graph stats. Use knowledge_base command='links'
    on a specific page to explore its neighborhood.

    Args:
        project: Project ID to scope the graph. Omit for everything you can see.
        project_id: Deprecated alias for project.
    """
    data = await _get(
        "/v1/knowledge/wiki/graph",
        {"max_nodes": "30"},
        project=project,
        project_id=project_id,
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


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


@mcp.tool()
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
    params = scope_params(project=project_id or project)
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
            # The token as well as the ordinal. [1] is readable prose but names
            # nothing outside this one answer, and the instructions above tell a
            # writer to reuse these keys in sections[].citations -- so the key
            # has to survive the rendering that makes the answer readable.
            lines.append(f"  [{i}] {{{{{key}}}}}  ({source_type}) {title}")
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

    return {
        "content": "\n".join(lines),
        "sources": [
            _public_source(src, citation_key=key)
            for key, src in sources.items()
            if isinstance(src, dict)
        ],
        "confidence": confidence or None,
        "gaps": gaps or None,
    }


# ---------------------------------------------------------------------------
# Proposal-based writes
# ---------------------------------------------------------------------------


@mcp.tool()
async def knowledge_base_write(
    action: str,
    arguments: dict | None = None,
    scope: str | None = None,
) -> dict:
    """Propose a knowledge-base change. Nothing is written until accept_proposal.

    Pass the action as ``action`` and its fields as a flat ``arguments`` object.
    Every change except page_type needs ``rationale``; edits also take ``edit_note``.

      edit_section {page, section_id, new_section_body, citations?, section_meta?,
                    section_title?, after?}  PREFERRED for updating a page. Replaces one
                    section's whole body; a new section_id plus section_title adds one.
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
    body = dict(arguments or {})
    body["action"] = action
    if scope:
        body["scope"] = scope
    return _wiki_result(await _post("/v1/knowledge/wiki/write", body))


@mcp.tool()
async def show_proposal(proposal_id: str) -> str:
    """Show one wiki proposal before accepting or dismissing it."""
    data = await _get(f"/v1/knowledge/wiki/proposals/{proposal_id}")
    return _format_proposal(data) + "\n\nPayload:\n" + json.dumps(data.get("payload", {}), indent=2)


@mcp.tool()
async def accept_proposal(proposal_id: str) -> str:
    """Accept and apply a wiki proposal.

    Only call this after the user explicitly asks to accept/apply this specific
    proposal. Do not create and accept a proposal in the same assistant step
    unless the user already explicitly requested that behavior.
    """
    data = await _post(f"/v1/knowledge/wiki/proposals/{proposal_id}/accept")
    return f"Accepted proposal {data.get('proposal_id', proposal_id)}."


@mcp.tool()
async def dismiss_proposal(proposal_id: str) -> str:
    """Dismiss a wiki proposal after the user asks to reject it."""
    data = await _post(f"/v1/knowledge/wiki/proposals/{proposal_id}/dismiss")
    return f"Dismissed proposal {data.get('proposal_id', proposal_id)}."


# ---------------------------------------------------------------------------
# Version and self-update
# ---------------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
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
        lines.append("Latest release: unknown (could not reach PyPI).")
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
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )
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
        return (
            f"No update needed: installed {status.current}, latest release {status.latest}."
        )
    info = detect_install()
    result = await asyncio.to_thread(run_upgrade, info)
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
