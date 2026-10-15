"""What the stdio MCP server sends back for a tool call: one text block.

Every tool here builds the shared result dict (``{content, coverage, next,
sources?, ...}``, :mod:`beakr_cli.mcp_result`). The server used to declare an
``outputSchema`` and send that dict twice: serialized as JSON in a text block,
and again as ``structuredContent``. Measured on agent evaluation traces, that doubled every
Knowledge result: Codex printed both copies into an exec cell it then cut at
about 10k tokens, and Claude Code refused 41% of results for being over its
~50k-character limit. Neither client gave the model anything from the second
copy that the first did not already hold.

So a result is sent once, as text a model reads directly:

- the tool's ``content``, exactly as the engine rendered it;
- for the Knowledge tools, a compact ``Sources`` list: one line per cited
  document (where it lives -- its path, or a connector item's place in its
  service --, every place cited in it -- ``p. 7``, ``slide 3``, ``sheet 2,
  B4:D9`` --, provider, url, the file id that ``read_file`` opens, and the
  ``source_ref`` a write cites, and the plain ``citation_key`` that pins the
  exact cited version/passage) with one verbatim excerpt of at most
  :data:`EXCERPT_CHARS`, capped at :data:`SOURCES_CHARS` in all. No inline
  ``{{token}}`` markers: the text cites each passage by its readable reference
  (``memo.pdf › p. 7``);
- the ``next`` hints when the result is incomplete or names a follow-up.

No ``outputSchema`` is declared, so no client expects ``structuredContent``
(the MCP spec requires it whenever a schema is declared). Errors are the same
one block: ``{status, error_code, message, fix}`` as compact JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Verbatim excerpt shown per cited document. 300 characters held 87-90% of
#: the evidence answers used (excerpt-tuning.md, Q3).
EXCERPT_CHARS = 300

# Shared with the hosted renderer and its file-read formatter.
LOCATION_SEPARATOR = " › "

#: The whole Sources list stays under this; the rest is one ``sources`` call away.
SOURCES_CHARS = 8_000

#: Cited places and version-pinned keys listed per document before "+N more".
PLACES_PER_DOCUMENT = 6
CITATION_KEYS_PER_DOCUMENT = 6

#: Tools whose ``content`` names sources only by their readable reference, so
#: the list says where each document lives and how to open and cite it.
#: ``research`` renders its own Sources list in content.
_SOURCE_LISTING_TOOLS = frozenset({"knowledge_base", "knowledge_base_write"})

#: The provider of a file uploaded to Beakr (the engine's ``public_sources.UPLOAD_PROVIDER``).
UPLOAD_PROVIDER = "upload"


def render(tool: str, body: dict[str, Any]) -> str:
    """The one text block a tool's result is sent as."""
    text = str(body.get("content") or "").rstrip()
    parts = [text] if text else []
    # Text is the only payload the model receives. Dropping coverage here made
    # incomplete scans look exhaustive, even when the backend reported otherwise.
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


def _one_line(value: Any, width: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


_VERSION_CITATION = re.compile(r"^(?P<kind>artifact|unit):(?P<id>[0-9a-fA-F]{8}-[0-9a-fA-F-]{27})")


def _document_key(record: dict[str, Any]) -> str:
    # The citation key carries the exact version. Prefer it to a file id so two
    # cited versions of the same file are never merged into one Sources entry.
    citation_key = str(record.get("citation_key") or "")
    match = _VERSION_CITATION.match(citation_key)
    if match:
        return f"{match.group('kind')}:{match.group('id')}"
    location = record.get("location") or {}
    return str(
        location.get("file_id")
        or location.get("url")
        or location.get("display_path")
        or record.get("filename")
        or record.get("name")
        or citation_key
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
            _document_key(record),
            {"record": record, "places": [], "citation_keys": [], "excerpt": None},
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
        for key in [
            record.get("citation_key"),
            *(excerpt.get("citation_key") for excerpt in excerpts),
        ]:
            if key and str(key) not in entry["citation_keys"]:
                entry["citation_keys"].append(str(key))
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
    status = record.get("status") or {}
    if status.get("changed_since_cited"):
        where.append("source changed since cited; use citation_key for the cited version")
    keys = entry.get("citation_keys") or []
    if keys:
        shown_keys = ", ".join(keys[:CITATION_KEYS_PER_DOCUMENT])
        if len(keys) > CITATION_KEYS_PER_DOCUMENT:
            shown_keys += f" +{len(keys) - CITATION_KEYS_PER_DOCUMENT} more"
        label = "citation_key" if len(keys) == 1 else "citation_keys"
        where.append(f"{label} {shown_keys}")
    passages = record.get("cited_passages")
    if isinstance(passages, int) and passages > 1:
        where.append(f"{passages} passages cited")
    if record.get("source_ref"):
        where.append(f"source_ref {record['source_ref']}")
    tail = f" · {' · '.join(where)}" if where else ""
    head = f"{name}{LOCATION_SEPARATOR}{shown}" if shown else name
    lines = [f"- {head}{tail}".rstrip()]
    if entry["excerpt"]:
        lines.append(f'  "{_one_line(entry["excerpt"], EXCERPT_CHARS)}"')
    return lines
