"""What the stdio MCP server sends back for a tool call: one text block.

Every tool here builds the shared result dict (``{content, coverage, next,
sources?, ...}``, :mod:`beakr_cli.mcp_result`). The server used to declare an
``outputSchema`` and send that dict twice: serialized as JSON in a text block,
and again as ``structuredContent``. Measured on the Harvey LAB traces
(scratchpad/dogfood/excerpt-tuning.md, 2026-10-13), that doubled every
Knowledge result: Codex printed both copies into an exec cell it then cut at
about 10k tokens, and Claude Code refused 41% of results for being over its
~50k-character limit. Neither client gave the model anything from the second
copy that the first did not already hold.

So a result is sent once, as text a model reads directly:

- the tool's ``content``, exactly as the engine rendered it;
- for the Knowledge tools, a compact ``Sources`` list: one line per cited
  document (its ``{{token}}``s each with where that passage sits -- ``p. 7``,
  ``slide 3``, ``sheet 2, B4:D9`` --, where the document lives -- its path, or
  a connector item's place in its service --, provider, the file id that
  ``read_file`` opens) with one verbatim excerpt of at most
  :data:`EXCERPT_CHARS`, capped at :data:`SOURCES_CHARS` in all;
- the ``next`` hints when the result is incomplete or names a follow-up.

No ``outputSchema`` is declared, so no client expects ``structuredContent``
(the MCP spec requires it whenever a schema is declared). Errors are the same
one block: ``{status, error_code, message, fix}`` as compact JSON.
"""

from __future__ import annotations

import json
from typing import Any

#: Verbatim excerpt shown per cited document. 300 characters held 87-90% of
#: the evidence answers used (excerpt-tuning.md, Q3).
EXCERPT_CHARS = 300

# Shared with the hosted renderer and its file-read formatter.
LOCATION_SEPARATOR = " › "

#: The whole Sources list stays under this; the rest is one ``sources`` call away.
SOURCES_CHARS = 8_000

#: Citation tokens listed per document before "+N more".
TOKENS_PER_DOCUMENT = 4

#: Tools whose ``content`` names sources only by ``{{token}}``, so the list says
#: what each token is. ``research`` renders its own numbered Sources in content.
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
    """``Sources (N documents, M Knowledge pages):`` then one entry per cited source."""
    documents: dict[str, dict[str, Any]] = {}
    for record in sources:
        if not isinstance(record, dict):
            continue
        entry = documents.setdefault(
            _document_key(record), {"record": record, "tokens": [], "units": {}, "excerpt": None}
        )
        # A full record carries one passage (``evidence``); a compact one carries
        # its document's passages as ``excerpts``, each with its own token. Each
        # passage says where it sits in the document (``unit_locator``).
        passages = [
            (
                record.get("citation_key"),
                (record.get("evidence") or {}).get("excerpt"),
                (record.get("location") or {}).get("unit_locator"),
            ),
            *(
                (excerpt.get("citation_key"), excerpt.get("text"), excerpt.get("unit_locator"))
                for excerpt in record.get("excerpts") or []
                if isinstance(excerpt, dict)
            ),
        ]
        for key, excerpt, unit in passages:
            if key and str(key) not in entry["tokens"]:
                entry["tokens"].append(str(key))
            if key and unit:
                entry["units"].setdefault(str(key), str(unit))
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
    """``- <path>: {{token}} #<unit> ... · read_file <id>``, then one excerpt.

    The path leads and each token is followed by its place, so ``<path> › <unit>``
    -- what ``read_file`` opens -- sits next to the token a writer cites. On the
    Harvey runs, tokens rewritten to ``path#part`` made Codex about twice as fast;
    the token stays because writes cite it.
    """
    record = entry["record"]
    location = record.get("location") or {}
    tokens = entry["tokens"]
    units = entry.get("units") or {}
    shown = " ".join(
        f"{{{{{t}}}}}" + (f" › {units[t]}" if units.get(t) else "")
        for t in tokens[:TOKENS_PER_DOCUMENT]
    )
    if len(tokens) > TOKENS_PER_DOCUMENT:
        shown += f" +{len(tokens) - TOKENS_PER_DOCUMENT} more"
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
    tail = f" · {' · '.join(where)}" if where else ""
    lines = [f"- {name}: {shown}{tail}".rstrip()]
    if entry["excerpt"]:
        lines.append(f'  "{_one_line(entry["excerpt"], EXCERPT_CHARS)}"')
    return lines
