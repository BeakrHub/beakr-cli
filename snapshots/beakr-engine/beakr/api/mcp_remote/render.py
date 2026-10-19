"""What the hosted MCP server sends back for a tool call: one text block.

Every tool builds the shared result dict (``{content, coverage, next, sources?,
...}``, :func:`beakr.api.mcp_remote.formatting.finish`). The server used to send
that dict twice: serialized as JSON in a text block, and again as
``structuredContent``. Measured on agent evaluation traces (2026-10-13), that
doubled every Knowledge result: Codex printed both copies into an exec cell it
then cut at about 10k tokens, and Claude Code refused results over its
~50k-character limit. Neither client gave the model anything from the second
copy that the first did not already hold.

So a result is sent once, as text a model reads directly, exactly as beakr-cli's
stdio server renders it (``beakr_cli.mcp_render``; its parity test asserts the
stdio text equals :func:`render` of the hosted dict):

- the tool's ``content``, exactly as the engine rendered it;
- for a write that staged a proposal, its ``content_hash``, which
  ``accept_proposal`` takes back as ``reviewed_hash``;
- for the Knowledge tools, a compact ``Sources`` list: one line per cited
  document (where it lives -- its path, or a connector item's place in its
  service --, every place cited in it -- ``p. 7``, ``slide 3``, ``sheet 2,
  B4:D9`` --, provider, url, the file id that ``read_file`` opens, and the
  ``source_ref`` a write cites) with one verbatim excerpt of at most
  :data:`EXCERPT_CHARS`, capped at :data:`SOURCES_CHARS` in all. No citation
  tokens: the text cites each passage by its readable reference
  (``memo.pdf › p. 7``);
- the ``next`` hints when the result is incomplete or names a follow-up.

No tool declares an ``outputSchema``, so no client expects ``structuredContent``
(the MCP spec requires it whenever a schema is declared). Errors are one block
too: ``{status, error_code, message, fix}`` as compact JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: The provider of a file Beakr holds itself. Equals
#: ``beakr.services.public_sources.UPLOAD_PROVIDER``; repeated here because this
#: module must import nothing from ``beakr`` (beakr-cli loads it standalone for
#: its parity test). tests/unit/test_mcp_render_standalone.py checks both.
UPLOAD_PROVIDER = "upload"

#: Between a source and the place inside it (``memo.pdf › p. 7``). Equals
#: ``beakr.services.source_locations.unit_locator.LOCATION_SEPARATOR``; repeated
#: for the same standalone reason as ``UPLOAD_PROVIDER``.
LOCATION_SEPARATOR = " \u203a "

#: Verbatim excerpt shown per cited document. 300 characters held 87-90% of
#: the evidence answers used (an excerpt-length measurement on evaluation runs).
EXCERPT_CHARS = 300

#: The whole Sources list stays under this; the rest is one ``sources`` call away.
SOURCES_CHARS = 8_000

#: Cited places listed per document before "+N more".
PLACES_PER_DOCUMENT = 6

#: Tools whose ``content`` names sources only by their readable reference, so
#: the list says where each document lives and how to open and cite it.
#: ``research`` renders its own Sources list in content.
_SOURCE_LISTING_TOOLS = frozenset({"knowledge_base", "knowledge_base_write"})

#: A hint every refused Knowledge call carries; the message already says it.
_GENERIC_FIXES = frozenset({"Correct the call as the message says and retry; nothing was read."})


def render(tool: str, body: dict[str, Any]) -> str:
    """The one text block a tool's result is sent as."""
    text = str(body.get("content") or "").rstrip()
    parts = [text] if text else []
    # Text is the only payload the model receives. Dropping coverage here made
    # incomplete scans look exhaustive, even when the backend reported otherwise.
    proposal = body.get("proposal")
    if isinstance(proposal, dict) and proposal.get("content_hash"):
        # A staged write's hash, the one accept_proposal takes back as
        # reviewed_hash: this text is all an MCP client receives.
        parts.append(f"content_hash: {proposal['content_hash']}")
    coverage = body.get("coverage") or {}
    if isinstance(coverage, dict) and (
        coverage.get("complete") is False
        or coverage.get("has_more")
        or coverage.get("truncated")
        or coverage.get("next_cursor") is not None
    ):
        parts.append("Coverage: " + json.dumps(coverage, ensure_ascii=False, default=str))
    if tool == "research" and body.get("research_usage"):
        parts.append("Research usage: " + json.dumps(body["research_usage"], ensure_ascii=False))
    sources = body.get("sources")
    if tool in _SOURCE_LISTING_TOOLS and isinstance(sources, list) and sources:
        listing = sources_block(sources)
        if listing:
            parts.append(listing)
    hints = [str(h) for h in body.get("next") or [] if str(h) and str(h) not in text]
    if hints:
        parts.append("\n".join(f"next: {hint}" for hint in hints))
    return "\n\n".join(parts) if parts else "(no output)"


def error_text(
    *,
    message: str,
    error_code: str | None = None,
    fix: str | None = None,
    hints: list[str] | None = None,
    request_id: str | None = None,
) -> str:
    """An error as compact JSON: ``{status, error_code, message, fix}``.

    ``fix`` falls back to the result's own hints when the route sent none.
    """
    useful = [hint for hint in hints or [] if hint not in _GENERIC_FIXES]
    compact: dict[str, Any] = {"status": "error"}
    if error_code:
        compact["error_code"] = error_code
    compact["message"] = message.strip() or "The call failed."
    resolved_fix = fix or (" ".join(useful) or None)
    if resolved_fix:
        compact["fix"] = resolved_fix
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
        compact["request_id"] = request_id
    return json.dumps(compact, separators=(", ", ": "))


def body_error_text(body: dict[str, Any]) -> str:
    """:func:`error_text` for a tool result whose ``status`` is ``error``."""
    return error_text(
        message=str(body.get("message") or body.get("content") or ""),
        error_code=body.get("error_code"),
        fix=body.get("fix"),
        hints=[str(hint) for hint in body.get("next") or []],
        request_id=body.get("request_id"),
    )


def _one_line(value: Any, width: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _document_key(record: dict[str, Any]) -> str:
    location = record.get("location") or {}
    return str(
        location.get("file_id")
        or location.get("url")
        or location.get("display_path")
        or record.get("filename")
        or record.get("name")
        or record.get("citation_key")
        or id(record)
    )


def sources_block(sources: list[Any]) -> str:
    """``Sources (N documents, M Knowledge pages):`` then one entry per cited document.

    No citation tokens: the text names each citation by its readable reference
    (``memo.pdf › p. 7``), and this list says, once per document, where it
    lives, every place cited in it, and how to open (``read_file``) and cite
    (``source_ref``) it.
    """
    documents: dict[str, dict[str, Any]] = {}
    for record in sources:
        if not isinstance(record, dict):
            continue
        entry = documents.setdefault(
            _document_key(record), {"record": record, "places": [], "excerpt": None}
        )
        # A full record carries one passage (``evidence``) and its place; a
        # compact one carries its document's places (``cited_places``) and some
        # of its passages as ``excerpts``, each with its own place.
        location = record.get("location") or {}
        excerpts = [e for e in record.get("excerpts") or [] if isinstance(e, dict)]
        places = [
            location.get("unit_locator"),
            *(record.get("cited_places") or []),
            *(excerpt.get("unit_locator") for excerpt in excerpts),
        ]
        for place in places:
            if place and str(place) not in entry["places"]:
                entry["places"].append(str(place))
        for excerpt in [
            (record.get("evidence") or {}).get("excerpt"),
            *(excerpt.get("text") for excerpt in excerpts),
        ]:
            if excerpt and entry["excerpt"] is None:
                entry["excerpt"] = excerpt
    if not documents:
        return ""
    lines = [f"Sources ({_count_label(list(documents.values()))}):"]
    used = len(lines[0])
    shown = 0
    for entry in documents.values():
        block = _document_lines(entry)
        size = sum(len(line) + 1 for line in block)
        if shown and used + size > SOURCES_CHARS:
            break
        lines.extend(block)
        used += size
        shown += 1
    if shown < len(documents):
        lines.append(
            f"+{len(documents) - shown} more sources: knowledge_base command='sources' "
            "with the page lists every cited passage."
        )
    return "\n".join(lines)


def _is_knowledge_page(record: dict[str, Any]) -> bool:
    """A cited Knowledge (wiki) page, as opposed to a document or connector item."""
    return (
        str(record.get("source_kind") or "").lower() == "wiki"
        or str(record.get("citation_key") or "").startswith("wiki:")
        or str(record.get("source_ref") or "").startswith("beakr-source:v1:wiki:")
    )


def _count_label(entries: list[dict[str, Any]]) -> str:
    """``3 documents, 9 Knowledge pages``: a cat cites pages as well as files."""
    pages = sum(1 for entry in entries if _is_knowledge_page(entry["record"]))
    others = len(entries) - pages
    parts = []
    if others:
        parts.append(f"{others} document{'s' if others != 1 else ''}")
    if pages:
        parts.append(f"{pages} Knowledge page{'s' if pages != 1 else ''}")
    return ", ".join(parts)


def _document_lines(entry: dict[str, Any]) -> list[str]:
    """``- <path> › <places> · read_file <id> · source_ref <ref>``, then one excerpt.

    The path leads and every cited place follows it, so ``<path> › <place>`` --
    what ``read_file`` opens and a write cites as ``reference`` -- reads as one
    piece. On evaluation runs, citations rewritten to ``path#part`` made Codex
    about twice as fast as tokens did.
    """
    record = entry["record"]
    location = record.get("location") or {}
    places = entry.get("places") or []
    shown = ", ".join(places[:PLACES_PER_DOCUMENT])
    if len(places) > PLACES_PER_DOCUMENT:
        shown += f" +{len(places) - PLACES_PER_DOCUMENT} more"
    # Where the source lives: a Beakr file's drive path, a connector file's path
    # in its service, or a connector item's place there.
    name = str(
        location.get("display_path") or record.get("filename") or record.get("name") or "source"
    )
    where = []
    # ``upload`` says only that Beakr holds the file, which the read_file id says.
    if record.get("provider") and record["provider"] != UPLOAD_PROVIDER:
        where.append(str(record["provider"]))
    if location.get("url"):
        where.append(str(location["url"]))
    if location.get("file_id"):
        where.append(f"read_file {location['file_id']}")
    passages = record.get("cited_passages")
    if isinstance(passages, int) and passages > 1:
        where.append(f"{passages} passages cited")
    if record.get("source_ref"):
        where.append(f"source_ref {record['source_ref']}")
    tail = f" \u00b7 {' \u00b7 '.join(where)}" if where else ""
    head = f"{name}{LOCATION_SEPARATOR}{shown}" if shown else name
    lines = [f"- {head}{tail}".rstrip()]
    if entry["excerpt"]:
        lines.append(f'  "{_one_line(entry["excerpt"], EXCERPT_CHARS)}"')
    return lines
