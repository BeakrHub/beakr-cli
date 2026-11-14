"""Turn route responses into what an MCP client reads.

Mirrors the ``beakr-cli`` stdio server's rendering (``mcp_server.py`` and
``mcp_tools_platform.py``) so the same tool returns the same shape whichever
server an agent is connected to. Every result is bounded: an MCP client pays
for each character in context.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

from beakr.api.mcp_remote import render
from beakr.api.mcp_remote.document_listing import fit_document_listing
from beakr.engines.tools import agent_surface
from beakr.engines.tools.graph.kb_read_v2.listing_cut import cut_listing, with_continuation
from beakr.services.citation_filer import PUBLIC_FILER_FIELDS
from beakr.services.public_sources import UPLOAD_PROVIDER
from beakr.services.source_locations.sandbox_copies import source_path

MAX_TEXT_CHARS = 20_000
MAX_LIST_ITEMS = 50

#: The shared Knowledge tool text (beakr/engines/tools/agent_surface.py): Ask's
#: own descriptions plus the external calling convention. One source for this
#: server, the beakr-cli stdio server and Ask (W8.4).
KNOWLEDGE_BASE_DESCRIPTION = agent_surface.knowledge_base_description()
KNOWLEDGE_BASE_WRITE_DESCRIPTION = agent_surface.knowledge_base_write_description()

SOURCE_NOTE = (
    "\nCite with the readable reference in parentheses (memo.pdf \u203a p. 7); a Knowledge "
    "write accepts it as reference, or the source_ref above. Open the document "
    "(read_file, or its url) before quoting it or resting a conclusion on it."
)


def bounded(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n[truncated: {len(text) - limit:,} more characters]"


_CUT_MARKER = re.compile(r"\n\n\[truncated: [\d,]+ more characters\]\Z")


def _capped_at(text: str, limit: int) -> bool:
    """Whether ``text`` is what ``bounded`` makes at ``limit``: kept text plus its marker."""
    marker = _CUT_MARKER.search(text)
    return marker is not None and marker.start() <= limit


def as_list(result: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in keys:
            if isinstance(result.get(key), list):
                return result[key]
    return []


def page_of(result: Any, *keys: str) -> tuple[list[dict[str, Any]], bool, str | None]:
    """A REST page's rows, whether more follow, and the cursor that continues it."""
    rows = as_list(result, *keys)
    if not isinstance(result, dict):
        return rows, False, None
    cursor = result.get("next_cursor")
    has_more = bool(result.get("has_more")) and bool(cursor)
    return rows, has_more, (str(cursor) if has_more else None)


#: Character caps that differ from ``MAX_TEXT_CHARS``: Knowledge reads carry
#: whole sections, and a file read returns a 20,000-character window plus its
#: header and continuation line.
TEXT_LIMITS: dict[str, int] = {
    "knowledge_base": 50_000,
    "knowledge_base_write": 50_000,
    "read_file": 22_000,
}

#: What to do when a tool's text is cut, per tool. Each must stay useful when
#: the caller already narrowed the request, so it points at an untruncated path.
CUT_HINTS: dict[str, str] = {
    "knowledge_base": (
        "The text was cut at 50,000 characters: read a section (section_id), lines, or a "
        "smaller window (limit, offset)."
    ),
    "knowledge_base_write": (
        "The result text was cut at 50,000 characters: read the page with knowledge_base "
        "command='cat' to see it whole."
    ),
    "research": (
        "The answer was cut at the text limit: ask a narrower question, or read the cited "
        "pages with knowledge_base command='cat'."
    ),
    "context": (
        "The context was cut at the text limit: pass a smaller budget_chars, a project, or a "
        "question to narrow it."
    ),
    "workboard": (
        "The board was cut at the text limit, so some agents are missing: pass label_id to "
        "read one label's board, or project to read one project's."
    ),
    "wiki_graph": (
        "The graph summary was cut at the text limit: pass project to narrow it, or use "
        "knowledge_base command='links' on one page."
    ),
    "show_proposal": (
        "The proposal was cut at the text limit: read it whole with `beakr kb proposals show "
        "<proposal_id> --json` (GET /v1/knowledge/wiki/proposals/{proposal_id})."
    ),
    "get_profile": (
        "The profile was cut at the text limit: pass profile_type='project' with "
        "project_id to read one project's profile."
    ),
    "skills": (
        "The skill was cut at the text limit: open it in the Beakr web app to read it whole."
    ),
}

_DEFAULT_CUT_HINT = "The result was cut at the text limit: narrow the request."
_LIST_CUT_HINT = "The page was cut at the text limit: pass a smaller limit."


def _text_of(content: Any) -> str:
    """A result's text, from a string or a list of MCP content blocks."""
    if isinstance(content, list):
        return "\n\n".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict)
        )
    return "" if content is None else str(content)


def finish(result: Any, *, tool: str, cut_hint: str | None = None) -> dict[str, Any]:
    """Every MCP tool result in one shape: ``{content, coverage, next, ...}`` (SPEC-488).

    Both MCP servers pass each tool's result through this once. ``content`` is the
    text; ``coverage`` says how much came back (``returned``: rows for a list tool,
    characters for a file window, None otherwise), whether more exists
    (``has_more``, ``next_cursor``) and whether the result is ``complete``; ``next``
    holds the hints for getting the rest. Any other keys (``sources``, ``status``,
    ``file``...) are kept.

    The text cap is applied here and only here, and cutting text always marks the
    result incomplete with a hint, so a capped result can never claim to be whole
    (audit C077: the work board kept ``complete: true`` after losing agents).
    """
    body: dict[str, Any] = dict(result) if isinstance(result, dict) else {"content": result}
    text = _text_of(body.get("content"))
    coverage = dict(body.get("coverage") or {})
    coverage.setdefault("returned", None)
    coverage.setdefault("next_cursor", None)
    # A window that says where the next one starts has more after it: a Knowledge
    # read reports ``next_offset`` and no ``has_more``, which read as "no more".
    # So does a reply cut to a budget, which names the call that continues it
    # (``continue_with``) and has no cursor: ``next_cursor`` stays None.
    coverage.setdefault(
        "has_more",
        coverage["next_cursor"] is not None
        or coverage.get("next_offset") is not None
        or coverage.get("continue_with") is not None,
    )
    coverage.setdefault("complete", not coverage["has_more"])
    hints = [str(hint) for hint in body.get("next") or []]
    limit = TEXT_LIMITS.get(tool, MAX_TEXT_CHARS)
    if tool == "documents" and body.get("status") != "error" and not body.get("is_error"):
        window = fit_document_listing(text, limit)
        if window is not None:
            text = window["content"]
            if coverage.get("next_cursor") is not None:
                coverage["upstream_next_cursor"] = coverage["next_cursor"]
            coverage.update(window["coverage"])
            if window["next"] not in hints:
                hints.insert(0, window["next"])
    # The cap is enforced on the text itself, never on an incoming flag: an
    # upstream ``coverage.truncated`` only says the upstream cut something, not
    # that this cap was applied (audit C077 follow-up: a 90k Knowledge result
    # flagged truncated upstream passed the 50k cap untouched). Text this cap
    # already cut (a helper ran ``finish`` before the server did) is left as is,
    # so shaping twice cannot shorten the marker and misreport what was dropped.
    if len(text) > limit and not _capped_at(text, limit):
        text = bounded(text, limit)
        coverage["complete"] = False
        coverage["truncated"] = True
        hint = cut_hint or CUT_HINTS.get(tool, _DEFAULT_CUT_HINT)
        if hint not in hints:
            hints.insert(0, hint)
    if (
        tool == "knowledge_base"
        and coverage.get("command") == "timeline"
        and _capped_at(text, limit)
    ):
        # The backend cursor advances past its entire window, including events
        # lost to our text cap. Do not offer that cursor or a partial event as
        # evidence. Replay the original request so the unseen tail is recoverable.
        body["upstream_coverage"] = dict(coverage)
        coverage.update(returned=0, complete=False, truncated=True, next_cursor=None)
        coverage["next_offset"] = None
        text = (
            "Timeline window exceeded the response text limit; no event evidence is "
            "shown. Replay the original request with a smaller limit and the same "
            "input cursor (omit cursor if the original request had none). Do not "
            "advance to the backend's next cursor: it skips events not shown here. "
            "For a single oversized event, set include_content=false, then read its "
            "cited page or section separately."
        )
        hints = ["Replay this window before advancing; preserve the original filters and query."]
    body["content"] = text
    body["coverage"] = coverage
    body["next"] = hints
    return body


def file_read(data: dict[str, Any]) -> dict[str, Any]:
    """One window of a file's text (``POST /v1/artifacts/read``), with how to continue.

    The header names where the window is -- the file's path, and the place in it
    when the reference named one (``memo.pdf › p. 7``, a form ``read_file`` takes) --
    and the file and
    version read, so a transcript records which version it was.
    """
    file = data.get("file") or {}
    unit = data.get("unit") or None
    text = str(data.get("text") or "")
    start, end = int(data.get("start") or 0), int(data.get("end") or 0)
    total = data.get("total_chars")
    where = str(file.get("display_path") or file.get("title") or "File")
    hints: list[str] = []
    # An offset or a place past the end of the text: the route's plain statement
    # of it (the text's length, the last valid offset), in place of any text.
    past_end = str(data.get("note") or "") if data.get("past_end") else ""
    if unit is not None:
        locator = unit.get("locator")
        if locator:
            where += f"{render.LOCATION_SEPARATOR}{locator}"
        page = unit.get("page")
        span = f"page {page} of {unit.get('page_count')}, " if page else ""
        position = f"{span}characters {start}-{end}" + (f" of {total}" if total else "")
        if past_end:
            hints.append(past_end)
        elif not unit.get("found", True):
            hints.append("That place was not found in this version of the file; this is its start.")
        if data.get("next_offset") is not None:
            # The rest of this page or sheet, before the next one.
            hints.append(
                f"More of this place follows: call read_file with {where!r} and offset={end}."
            )
        if data.get("next_reference"):
            hints.append(f"Next page: call read_file with {data['next_reference']!r}.")
    else:
        position = f"characters {start}-{end}" + (f" of {total}" if total else "")
        if past_end:
            hints.append(past_end)
        if data.get("next_offset") is not None:
            hints.append(f"More text follows: call read_file again with offset={end}.")
    header = f"{where} ({position}; file {file.get('id')} version {file.get('version_id')})"
    more = bool(data.get("has_more"))
    # No blank stretch where a window past the end has no text.
    body = "\n\n".join([header, *([text] if text or not past_end else []), *hints])
    return finish(
        {
            "content": body,
            "coverage": {
                "returned": len(text),
                "has_more": more,
                "next_cursor": None,
                "complete": not more,
                "next_offset": data.get("next_offset"),
                "total_chars": total,
            },
            "next": hints,
            "file": {
                "artifact_id": str(file.get("id") or ""),
                "version_id": str(file.get("version_id") or ""),
                "title": file.get("title"),
                "display_path": file.get("display_path"),
                "unit_locator": (unit or {}).get("locator"),
                "offset": start,
                "next_offset": data.get("next_offset"),
                "total_chars": total,
            },
        },
        tool="read_file",
    )


def page_result(
    text: str,
    *,
    tool: str,
    returned: int,
    has_more: bool,
    next_cursor: str | None,
    same_args: str = "",
) -> dict[str, Any]:
    """One bounded page of a list or search tool (developer platform W8, SPEC-488).

    Every list tool on both MCP servers answers this shape: the rendered page,
    ``coverage`` (how many rows came back, whether more exist and the cursor that
    reaches them) and ``next`` hints. The continuation is also written into the
    text, so a client that shows only text still learns how to page on.
    """
    hints: list[str] = []
    if has_more and next_cursor:
        keep = f" {same_args}" if same_args else ""
        hints.append(f'More results: call {tool} again with cursor="{next_cursor}"{keep}.')
    body_text = text + (f"\n\n[{hints[0]}]" if hints else "")
    return finish(
        {
            "content": body_text,
            "coverage": {
                "returned": returned,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "complete": not has_more,
            },
            "next": hints,
        },
        tool=tool,
        cut_hint=_LIST_CUT_HINT,
    )


def capped_result(
    text: str, *, tool: str, returned: int, total: int | None, complete: bool, hint: str
) -> dict[str, Any]:
    """A result the route cannot page: what was shown, and how to narrow it.

    ``complete`` is the caller's row count; ``finish`` still marks the result
    incomplete when the text itself has to be cut.
    """
    hints = [] if complete else [hint]
    body_text = text + (f"\n\n[{hint}]" if hints else "")
    coverage: dict[str, Any] = {
        "returned": returned,
        "has_more": not complete,
        "next_cursor": None,
        "complete": complete,
    }
    if total is not None:
        coverage["total"] = total
    return finish({"content": body_text, "coverage": coverage, "next": hints}, tool=tool)


def error_text(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if body.get("detail"):
            return str(body["detail"])
    return "The request was refused."


def expected_base_index(body: Any) -> int | None:
    if not isinstance(body, dict):
        return None
    for container in (body.get("error"), body.get("detail")):
        if isinstance(container, dict) and isinstance(container.get("expected_base_index"), int):
            return int(container["expected_base_index"])
    return None


def project_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No projects found."
    lines = []
    for project in items[:MAX_LIST_ITEMS]:
        desc = project.get("description") or ""
        lines.append(
            f"- {project.get('name', 'Untitled')} ({project.get('project_type', 'standard')}, "
            f"id: {project.get('id', '')}){f' -- {desc}' if desc else ''}"
        )
        boundary = project.get("boundary")
        if isinstance(boundary, dict) and boundary.get("summary"):
            # Project boundaries: what touching it does to this session.
            lines.append(f"  Boundary: {boundary['summary']}")
    return "\n".join(lines)


def profile(data: Any, profile_type: str) -> str:
    """A profile as text: each list one item per line.

    Goals and focus areas were joined with ", ", but a goal is a sentence with
    commas of its own, so two goals read as one run-on list (Y13: "Track
    sequencing runs, samples, variants and assay results, Find which compounds
    ..."). The industry and entity types were left out.
    """
    if not data:
        return f"No {profile_type} profile found."
    lines = [f"# {data.get('name', 'Untitled')} ({profile_type} profile)"]
    if data.get("description"):
        lines.append(str(data["description"]))
    if data.get("industry"):
        lines.append(f"\nIndustry: {data['industry']}")
    for key, label in (
        ("goals", "Goals"),
        ("focus_areas", "Focus areas"),
        ("entity_types", "Entity types"),
    ):
        items = [str(item) for item in data.get(key) or [] if str(item).strip()]
        if items:
            lines.append(f"\n{label}:")
            lines.extend(f"  - {item}" for item in items)
    for key, label in (
        ("core_people", "Key people"),
        ("core_organizations", "Key organizations"),
        ("core_topics", "Key topics"),
    ):
        if data.get(key):
            lines.append(f"\n{label}:")
            for item in data[key]:
                if isinstance(item, dict):
                    detail = item.get("role") or item.get("type")
                    lines.append(f"  - {item.get('name', '')}" + (f" ({detail})" if detail else ""))
    if data.get("extraction_guidance"):
        lines.append(f"\nGuidance: {data['extraction_guidance']}")
    return "\n".join(lines)


def _result_text(result: Any) -> str:
    blocks = result.get("content") if isinstance(result, dict) else None
    if isinstance(blocks, list):
        texts = [
            str(block["text"])
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        if texts:
            return "\n".join(texts)
    return json.dumps(result, indent=2, default=str)


def public_source(source: dict, *, citation_key: str | None = None) -> dict:
    """Keep only the public source contract; never forward backend row fields."""
    meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
    nested = {
        key: source.get(key) if isinstance(source.get(key), dict) else {}
        for key in ("location", "evidence", "status", "section", "retrieval")
    }

    def first(*keys: str):
        for key in keys:
            for container in (source, meta):
                value = container.get(key)
                if value not in (None, "", []):
                    return value
        return None

    name = str(first("name", "source_title", "title") or "Untitled source")
    kind = first("source_kind", "unit_kind", "type", "source_type")
    # Never the run's sandbox copy of a downloaded file (sandbox_copies.py).
    path = nested["location"].get("display_path") or source_path(source)
    filename = first("filename", "file_name")
    if not filename and path:
        filename = PurePosixPath(str(path).rstrip("/")).name
    if not filename and str(kind or "").lower() in {"file", "document", "artifact"}:
        filename = name
    url = nested["location"].get("url") or first("url", "web_view_url", "full_text_url", "pdf_url")
    file_id = nested["location"].get("file_id") or first("file_id", "artifact_id")
    provider = first("provider", "source_provider")
    if not provider and file_id and not url:
        provider = UPLOAD_PROVIDER
    item: dict[str, Any] = {
        "source_ref": first("source_ref"),
        "citation_key": citation_key or first("citation_key", "key"),
        "name": name,
        "filename": filename,
        "provider": provider,
        "source_kind": kind,
        "stance": first("stance"),
    }
    groups = {
        "location": {
            "url": url,
            "display_path": path,
            "unit_locator": nested["location"].get("unit_locator"),
            "upload_path": nested["location"].get("upload_path"),
            "provider_item_id": nested["location"].get("provider_item_id")
            or first("provider_file_id", "remote_file_id"),
            # The public Files id (read_file / GET /v1/artifacts/{id}); see
            # public_source_descriptor for why it, alone of the ids, is public.
            "file_id": str(file_id) if file_id else None,
            # The file version the passage was read from, when the record names it
            # (a documents search result); download-url signs it.
            "version_id": nested["location"].get("version_id"),
        },
        "evidence": {
            key: nested["evidence"].get(key) or first(*aliases)
            for key, aliases in (
                ("excerpt", ("excerpt", "excerpt_text", "quote")),
                ("locator", ("locator", "chunk_ref")),
                ("fidelity", ("fidelity",)),
                ("evidence_kind", ("evidence_kind",)),
                # ``authored`` marks the compiler's own analysis, which the
                # Sources list labels (paraphrase) rather than quoting.
                ("quote_origin", ("quote_origin",)),
                ("anchor_status", ("anchor_status",)),
            )
        },
        "status": {
            "observed_at": nested["status"].get("observed_at") or first("observed_at"),
            "availability": nested["status"].get("availability") or first("source_availability"),
            "changed_since_cited": nested["status"].get("changed_since_cited")
            if "changed_since_cited" in nested["status"]
            else first("source_moved"),
        },
        "section": {
            key: nested["section"].get(key) or first(alias)
            for key, alias in (
                ("title", "section_title"),
                ("event_start", "event_start"),
                ("event_end", "event_end"),
                ("date_precision", "date_precision"),
            )
        },
    }
    for key, value in groups.items():
        cleaned = {k: v for k, v in value.items() if v not in (None, "")}
        if cleaned:
            item[key] = cleaned
    # A compact record (``kb_read_v2/compact_sidecar.py``) carries its document's
    # passages as ``excerpts``, each with its token and place, and how many it
    # cites. Dropping them here left the Sources footer with no excerpt at all.
    excerpts = [
        {
            **{
                key: str(excerpt[key])
                for key in ("citation_key", "section_id", "unit_locator", "text")
                if excerpt.get(key) not in (None, "")
            },
            # The compiler's own analysis, never to be printed as a quote.
            **({"authored": True} if excerpt.get("authored") is True else {}),
        }
        for excerpt in source.get("excerpts") or []
        if isinstance(excerpt, dict) and excerpt.get("text")
    ]
    if excerpts:
        item["excerpts"] = excerpts
    if isinstance(source.get("cited_passages"), int):
        item["cited_passages"] = source["cited_passages"]
    # Every place the read cites in the document, and the name its text uses for
    # it (``kb_read_v2/readable_view.py``): the Sources list prints both.
    places = [str(place) for place in source.get("cited_places") or [] if place]
    if places:
        item["cited_places"] = places
    if source.get("reference"):
        item["reference"] = str(source["reference"])
    retrieval = nested["retrieval"]
    arguments = retrieval.get("arguments")
    if (
        isinstance(retrieval.get("tool"), str)
        and isinstance(arguments, dict)
        and isinstance(arguments.get("handle"), str)
        and arguments["handle"]
    ):
        item["retrieval"] = {
            "tool": retrieval["tool"],
            "arguments": {"handle": arguments["handle"]},
        }
    else:
        from beakr.engines.tools.graph.native_source_fetch import public_native_call

        # A text connector's item (a Granola note, an email or Slack thread):
        # the provider read tool and its own id, which the Sources list prints
        # as the call that opens it (``render._read_call``).
        native = public_native_call(retrieval.get("tool"), arguments)
        if native is not None:
            item["retrieval"] = native
    # Who wrote an inline conversation citation, in the record's public form:
    # names, agent, client, run and session, never the person's id
    # (``citation_filer.PUBLIC_FILER_FIELDS``). Left out of this allowlist, the
    # writer reached no MCP, SDK or CLI source record though the contract
    # (``SourceRecord.filed_by``) declares it.
    filed_by = source.get("filed_by")
    if isinstance(filed_by, dict):
        public_filed = {
            field: str(filed_by[field])
            for field in (*PUBLIC_FILER_FIELDS, "text")
            if filed_by.get(field) not in (None, "")
        }
        if public_filed:
            item["filed_by"] = public_filed
    return {key: value for key, value in item.items() if value not in (None, "")}


def wiki_result(result: Any, *, tool: str = "knowledge_base") -> dict:
    """Canonical text plus the sanitized structured sources (the stdio shape).

    A read also carries the engine's ``coverage`` and ``next`` hints
    (``beakr/engines/tools/graph/kb_result_coverage.py``). ``finish`` applies this
    server's character bound: when it cuts the text, coverage says the result is
    incomplete and the first hint says how to get the rest.
    """
    result = result if isinstance(result, dict) else {}
    outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else {}
    body: dict[str, Any] = {
        "content": _result_text(result),
        "sources": [public_source(s) for s in result.get("sources", []) if isinstance(s, dict)],
        "status": outcome.get("status", "ok"),
    }
    if outcome.get("error_code"):
        body["error_code"] = outcome["error_code"]
    if isinstance(result.get("coverage"), dict):
        body["coverage"] = dict(result["coverage"])
    if isinstance(result.get("proposal"), dict):
        # A staged write's id and content hash, so the caller can accept it.
        body["proposal"] = dict(result["proposal"])
    state = _approval_state(result)
    if state:
        # pending | auto_apply_queued | needs_person (wiki_approval_state), wherever
        # the reply puts it; render prints it unless the text already names it.
        body["approval_state"] = state
    body["next"] = [str(h) for h in result.get("next") or []]
    if tool == "knowledge_base":
        _fit_listing(body)
    return finish(body, tool=tool)


def _approval_state(result: dict[str, Any]) -> str | None:
    """A write reply's ``approval_state``: top level, ``outcome`` or ``proposal``; else None."""
    outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else {}
    proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    for holder in (result, outcome, proposal):
        state = holder.get("approval_state")
        if isinstance(state, str) and state.strip():
            return state.strip()
    return None


def _fit_listing(body: dict[str, Any]) -> None:
    """Cut a Knowledge listing over the text cap at whole items, before ``finish`` would.

    ``finish`` cuts by characters, which left a listing's range and offset
    describing rows that were cut (kb_read_v2/listing_cut.py has the root
    cause). Done here rather than in ``finish``: ``finish`` is shared byte for
    byte with beakr-cli's stdio server, whose door replies arrive already cut to
    the door's budget.
    """
    text = str(body.get("content") or "")
    limit = TEXT_LIMITS["knowledge_base"]
    coverage = dict(body.get("coverage") or {})
    if len(text) <= limit:
        return
    command = str(coverage.get("command") or "")
    listing = cut_listing(command, text, limit, coverage)
    if listing is None:
        return
    body["next"] = with_continuation(command, body["next"], coverage, listing.hint)
    body["content"] = listing.text
    body["coverage"] = {**coverage, **listing.coverage}


def _graph_parts(data: dict) -> tuple[list[dict], list[dict], int]:
    """The page nodes, the links among them, and how many source-document nodes there are.

    The graph route returns the pages it selected AND a node per source document
    those pages cite, with an edge from each document to each page citing it.
    Counting every node as a page reported "392 pages, 518 links" for a request
    capped at 30 pages (Coding Agent Work, 2026-11-06): 30 pages, the documents
    they cite, and the citation edges.
    """
    nodes = [node for node in data.get("nodes", []) if isinstance(node, dict)]
    pages = [node for node in nodes if node.get("type") != "source"]
    page_ids = {node.get("id") for node in pages}
    links = [
        edge
        for edge in data.get("edges", [])
        if isinstance(edge, dict)
        and edge.get("type") != "source"
        and edge.get("from") in page_ids
        and edge.get("to") in page_ids
    ]
    return pages, links, len(nodes) - len(pages)


def graph(data: dict, *, total_pages: int | None = None) -> str:
    pages, links, sources = _graph_parts(data)
    connections: dict[str, int] = {}
    for edge in links:
        for end in (edge.get("from"), edge.get("to")):
            connections[end] = connections.get(end, 0) + 1
    of_total = f" of {total_pages}" if total_pages is not None else ""
    head = f"Graph: the {len(pages)} most linked-to pages{of_total}, {len(links)} links among them"
    if sources:
        head += f", citing {sources} source documents"
    lines = [f"{head}\n", "Pages, by links among the pages shown:"]
    ranked = sorted(
        pages,
        key=lambda node: (-connections.get(node.get("id"), 0), str(node.get("label") or "")),
    )
    for node in ranked:
        label = node.get("label") or str(node.get("id"))[:8]
        count = connections.get(node.get("id"), 0)
        lines.append(f"  {label} ({node.get('page_type') or ''}) - {count} links")
    return "\n".join(lines)


#: Pages the graph summary asks for; a graph that returns this many may have more.
GRAPH_MAX_NODES = 30


def graph_result(data: dict, *, total_pages: int | None = None) -> dict[str, Any]:
    """The graph summary, its coverage counted in pages (never source-document nodes).

    ``total_pages`` is the scope's live page count (``wiki_stats``): with it the
    result is complete only when every page was shown.
    """
    data = data if isinstance(data, dict) else {}
    pages, _, _ = _graph_parts(data)
    if total_pages is not None:
        complete = len(pages) >= total_pages
    else:
        complete = len(pages) < GRAPH_MAX_NODES
    of_total = f" of {total_pages}" if total_pages is not None else ""
    return capped_result(
        graph(data, total_pages=total_pages),
        tool="wiki_graph",
        returned=len(pages),
        total=total_pages,
        complete=complete,
        hint=(
            f"Showing the {len(pages)} most linked-to pages{of_total} (the summary asks for "
            f"at most {GRAPH_MAX_NODES}): pass project to narrow it, or use knowledge_base "
            "command='links' on one page."
        ),
    )


def stats(data: dict) -> str:
    """``wiki_stats``: what each count counts.

    ``sources`` is the route's count of page-to-source citations, so one
    document cited by three pages counts three times; ``links`` is org-wide
    whatever the project, so it is not shown beside project counts.
    """
    data = data if isinstance(data, dict) else {}
    return (
        f"Pages: {data.get('pages', 0)} (live pages in scope)\n"
        f"Sources: {data.get('sources', 0)} (page-to-source citations: a document cited "
        "by three pages counts three times)"
    )


def _sources_listing(citations: list[dict], *, mode: str = "full", named_in: str = "") -> list[str]:
    """``Sources (N documents):`` then one entry per cited document, within ``SOURCES_CHARS``."""
    block = render.sources_block(
        [public_source(record) for record in citations], mode=mode, named_in=named_in
    )
    return ["\n---\n" + block] if block else []


def research(data: dict, *, sources: str = "full") -> dict:
    """A research answer: its text, citing each document by its readable reference, and the documents.

    The REST answer already shows each citation as the reference an outside
    reader opens and cites (``memo.pdf \u203a p. 7``) and keeps its records within
    the response budget (``services/public_answers.py``); the Sources list here
    is held to the same ``SOURCES_CHARS`` as a Knowledge read's. An answer
    without that public form (an older engine) is made readable here.

    ``sources`` is the tool's argument (``render.SOURCE_MODES``): ``brief`` lists
    each document on one line, leaving out Knowledge pages the answer names,
    ``none`` lists nothing, and ``full`` (the default here) the whole list.
    """
    from beakr.api.mcp_remote.research_metadata import attach_research_metadata, with_cited_labels
    from beakr.services.public_answers import public_answer

    data = with_cited_labels(data)
    citations = data.get("citations")
    text = data.get("text")
    next_hints = list(data.get("next") or [])
    if not (isinstance(citations, list) and isinstance(text, str)):
        sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
        readable = public_answer(
            str(data.get("answer") or ""),
            {str(key): source for key, source in sources.items() if isinstance(source, dict)},
        )
        text, citations = readable.text, readable.sources
        next_hints.extend(hint for hint in readable.next if hint not in next_hints)
    lines = [text] if text else []
    if citations and sources != "none":
        listing = _sources_listing(citations, mode=sources, named_in=text or "")
        if listing:
            lines.extend(listing)
            lines.append(SOURCE_NOTE)
    meta = [
        f"{label}: {data[k]}"
        for k, label in (("confidence", "Confidence"), ("gaps", "Gaps"))
        if data.get(k)
    ]
    if meta:
        lines.append("\n" + " | ".join(meta))
    return finish(
        attach_research_metadata(
            {
                "content": "\n".join(lines),
                "sources": [public_source(record) for record in citations],
                "next": next_hints,
                "confidence": data.get("confidence") or None,
                "gaps": data.get("gaps") or None,
            },
            data,
        ),
        tool="research",
    )


def match_skill(skills: list[dict], ref: str) -> dict | None:
    lowered = ref.lower()
    for skill in skills:
        if str(skill.get("id", "")) == ref or str(skill.get("slug", "")).lower() == lowered:
            return skill
    for skill in skills:
        if str(skill.get("name", "")).lower() == lowered:
            return skill
    return None


def skill_list(skills: list[dict]) -> str:
    if not skills:
        return "No skills in your library."
    lines = [
        f"- {s.get('slug', '')}: {s.get('name', '')}"
        + (f" -- {s['description']}" if s.get("description") else "")
        for s in skills[:MAX_LIST_ITEMS]
    ]
    lines.append("\nCall skills with a slug to read its SKILL.md.")
    return "\n".join(lines)


def skill_md(skill: dict, bundle: dict) -> str:
    files = bundle.get("files") or {}
    body = files.get("SKILL.md")
    if not body:
        return f"Skill '{skill.get('slug')}' has no SKILL.md."
    others = sorted(path for path in files if path != "SKILL.md")
    footer = (
        ("\n\nOther files in this skill: " + ", ".join(others[:MAX_LIST_ITEMS])) if others else ""
    )
    return str(body) + footer


def conversations(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No matching conversations."
    lines = []
    for item in items:
        when = str(item.get("updated_at") or item.get("created_at") or "")[:10]
        lines.append(
            f"- {item.get('title') or '(untitled)'} ({item.get('client', '')}, {when}, "
            f"id: {item.get('id')})"
        )
        status = item.get("compound_status")
        if status and status not in ("done", "pending", "none"):
            lines.append(f"  compound: {status}")
        if item.get("summary"):
            lines.append(f"  {' '.join(str(item['summary']).split())[:500]}")
    return "\n".join(lines)


def conversation_hits(hits: list[dict[str, Any]], *, note: str | None = None) -> str:
    """Index search hits, formatted like the beakr-cli stdio tool's.

    Each hit says how well it matches the query and ``note`` (the search's
    warning when no hit is a strong match) comes first, as ``kb sections`` and
    documents search print them.
    """
    if not hits:
        return "No matching conversations."
    lines = [note, ""] if note else []
    for hit in hits:
        title = hit.get("title") or "(untitled)"
        when = str(hit.get("updated_at") or "")[:10]
        kind = "imported session" if hit.get("kind") == "imported" else "Beakr chat"
        match = f", {hit['match']} match" if hit.get("match") else ""
        lines.append(f"- {title} ({kind}, {when}{match}, id: {hit.get('id')})")
        status = hit.get("compound_status")
        if status and status not in ("done", "pending", "none"):
            lines.append(f"  compound: {status}")
        if hit.get("summary"):
            lines.append(f"  {' '.join(str(hit['summary']).split())[:500]}")
        findings = hit.get("findings") or []
        if findings:
            titles = [f.get("page_title") for f in findings if f.get("page_title")]
            lines.append(
                f"  findings: {len(findings)}" + (f" ({', '.join(titles[:5])})" if titles else "")
            )
        outputs = hit.get("outputs") or []
        if outputs:
            lines.append("  outputs: " + ", ".join(str(o.get("title")) for o in outputs[:5]))
    return "\n".join(lines)


def audit(events: list[dict[str, Any]]) -> str:
    if not events:
        return "No audit events."
    return "\n".join(
        f"- {str(e.get('created_at') or '')[:19]} {e.get('action')} "
        f"by {e.get('actor_user_id') or 'system'}"
        + (f" on {e['target_resource_id']}" if e.get("target_resource_id") else "")
        for e in events
    )


def work_board(board: dict) -> dict[str, Any]:
    """The live Knowledge-agent plans in one scope, one block per agent (at most 50)."""
    rows = board.get("work") or []
    if not rows:
        return capped_result(
            "No Knowledge agent is working in this scope right now.",
            tool="workboard",
            returned=0,
            total=0,
            complete=True,
            hint="",
        )
    blocks = []
    for row in rows[:MAX_LIST_ITEMS]:
        lines = [
            f"- {row.get('agent_kind') or 'agent'} in {row.get('project_name') or 'project'}: "
            f"{row.get('state') or 'planning'}"
        ]
        if row.get("summary"):
            lines.append(f"  plan: {row['summary']}")
        if row.get("write_refs"):
            lines.append(f"  changing: {', '.join(map(str, row['write_refs']))}")
        if row.get("read_refs"):
            lines.append(f"  reading: {', '.join(map(str, row['read_refs']))}")
        if row.get("waiting_on"):
            waits = [
                str(item.get("target")) for item in row["waiting_on"] if isinstance(item, dict)
            ]
            lines.append(f"  waiting on: {', '.join(waits)}")
        blocks.append("\n".join(lines))
    shown = min(len(rows), MAX_LIST_ITEMS)
    return capped_result(
        "\n".join(blocks),
        tool="workboard",
        returned=shown,
        total=len(rows),
        complete=len(rows) <= MAX_LIST_ITEMS,
        hint=f"Showing {shown} of {len(rows)} agents: pass label_id to read one board.",
    )
