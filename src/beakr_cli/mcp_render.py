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
- for a write that staged a proposal, its ``content_hash``, which
  ``accept_proposal`` takes back as ``reviewed_hash``;
- for the Knowledge tools, a compact ``Sources`` list: one line per cited
  document (where it lives -- its path, or a connector item's place in its
  service --, every place cited in it -- ``p. 7``, ``slide 3``, ``sheet 2,
  B4:D9`` --, provider, url, the file id that ``read_file`` opens, and the
  ``source_ref`` a write cites) with one verbatim excerpt of at most
  :data:`EXCERPT_CHARS`, capped at :data:`SOURCES_CHARS` in all. No citation
  tokens: the text cites each passage by its readable reference
  (``memo.pdf › p. 7``). A read chooses how much of it to show with its
  ``sources`` argument (:data:`SOURCE_MODES`); see :func:`sources_block`;
- the ``next`` hints when the result is incomplete or names a follow-up.

No ``outputSchema`` is declared, so no client expects ``structuredContent``
(the MCP spec requires it whenever a schema is declared). Errors are the same
one block: ``{status, error_code, message, fix}`` as compact JSON.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, get_args

from pydantic import Field

#: Verbatim excerpt shown per cited document. 300 characters held 87-90% of
#: the evidence answers used (excerpt-tuning.md, Q3).
EXCERPT_CHARS = 300

# Shared with the hosted renderer and its file-read formatter.
LOCATION_SEPARATOR = " › "

#: The whole Sources list stays under this; the rest is one ``sources`` call away.
SOURCES_CHARS = 8_000

#: Cited places listed per document before "+N more".
PLACES_PER_DOCUMENT = 6

#: Tools whose ``content`` names sources only by their readable reference, so
#: the list says where each document lives and how to open and cite it.
#: ``research`` renders its own Sources list in content.
_SOURCE_LISTING_TOOLS = frozenset({"knowledge_base", "knowledge_base_write"})

#: The provider of a file uploaded to Beakr (the engine's ``public_sources.UPLOAD_PROVIDER``).
UPLOAD_PROVIDER = "upload"

#: How much of the Sources list a read shows, its ``sources`` argument:
#: ``none`` leaves it out, ``brief`` is one line per document with no excerpt
#: (Knowledge pages the text already names left out), ``full`` the whole list.
#: Described in the input schema itself: the tool descriptions are the engine's
#: shared text (``tool_surface``), which does not name this server's option.
SourceMode = Annotated[
    Literal["none", "brief", "full"],
    Field(
        description=(
            "The Sources list closing the result. brief (default): one line per cited "
            "document, its path, cited places and source_ref, leaving out Knowledge pages "
            "the text already names. full: every source with an excerpt, url and read_file "
            "id. none: no list."
        )
    ),
]
SOURCE_MODES: tuple[str, ...] = get_args(get_args(SourceMode)[0])

#: What the read tools show when not told. The full list closed every Knowledge
#: result with up to 8,000 characters, most of it excerpts of passages the text
#: had already quoted and Knowledge pages a listing had already printed.
DEFAULT_SOURCE_MODE = "brief"

#: The result key a read sets to its ``sources`` argument. A result without it
#: gets the full list, as the hosted server renders every result.
SOURCE_MODE_KEY = "source_listing"


def render(tool: str, body: dict[str, Any]) -> str:
    """The one text block a tool's result is sent as."""
    text = str(body.get("content") or "").rstrip()
    parts = [text] if text else []
    proposal = body.get("proposal")
    if isinstance(proposal, dict) and proposal.get("content_hash"):
        # A staged write's hash, the one accept_proposal takes back as
        # reviewed_hash: this text is all an MCP client receives.
        parts.append(f"content_hash: {proposal['content_hash']}")
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
    mode = body.get(SOURCE_MODE_KEY) or "full"
    if tool in _SOURCE_LISTING_TOOLS and isinstance(sources, list) and sources and mode != "none":
        listing = sources_block(sources, mode=mode, named_in=text)
        if listing:
            parts.append(listing)
    if tool == "knowledge_base_write":
        state = approval_state_line(body.get("approval_state"), text)
        if state:
            parts.append(state)
    hints = [str(h) for h in body.get("next") or [] if str(h) and str(h) not in text]
    if hints:
        parts.append("\n".join(f"next: {hint}" for hint in hints))
    return "\n\n".join(parts) if parts else "(no output)"


def organization_line(org: dict[str, Any], *, pinned_by: str | None = None) -> str:
    """``Organization: <name> (<slug>) -- <how this connection chose it>.``

    The first line of ``get_profile`` and ``list_projects`` on both MCP servers.
    A person in several orgs could not tell which one a connection was acting
    in: nothing an agent read named it. ``pinned_by`` says what fixed the org
    (the hosted ``/mcp/o/{slug}`` URL, the CLI's org setting, an API key); left
    out, the org is the person's active one, which follows the web app.
    """
    name = _one_line(org.get("name") or "Untitled", 120)
    slug = _one_line(org.get("slug") or "", 120)
    label = f"{name} ({slug})" if slug else name
    how = (
        f"pinned by {pinned_by}"
        if pinned_by
        else "your active organization, which follows the one you switch to in the Beakr web app"
    )
    return f"Organization: {label} -- {how}."


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


def sources_block(sources: list[Any], *, mode: str = "full", named_in: str = "") -> str:
    """``Sources (N documents, M Knowledge pages):`` then one entry per cited document.

    No citation tokens: the text names each citation by its readable reference
    (``memo.pdf › p. 7``), and this list says, once per document, where it
    lives, every place cited in it, and how to open (``read_file``) and cite
    (``source_ref``) it.

    ``mode='brief'`` lists each document on one line -- its path, the places
    cited in it and its ``source_ref``, no excerpt -- and leaves out the
    Knowledge pages ``named_in`` (the result's text) already names: a grep or
    ls registers every page it prints, and repeated them all here. ``full``
    (the default, and the hosted server's rendering) is the whole list.
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
    brief = mode == "brief"
    # What the text cites before the Knowledge pages it links: listed in read
    # order, a page's own sections and linked pages led, and the cap cut the
    # cited sources first (S18-9).
    entries = sorted(documents.values(), key=lambda entry: _kind(entry["record"]) == _PAGE)
    named = 0
    if brief:
        kept = [entry for entry in entries if not _page_named_in(entry["record"], named_in)]
        named = len(entries) - len(kept)
        entries = kept
        if not entries:
            return ""
    count = _count_label(entries)
    if named:
        count += f"; {named} Knowledge {'page' if named == 1 else 'pages'} named above not repeated"
    lines = [f"Sources ({count}):"]
    used = len(lines[0])
    shown = 0
    for entry in entries:
        block = [_brief_line(entry)] if brief else _document_lines(entry)
        size = sum(len(line) + 1 for line in block)
        if shown and used + size > SOURCES_CHARS:
            break
        lines.extend(block)
        used += size
        shown += 1
    if shown < len(entries):
        lines.append(
            f"+{len(entries) - shown} more sources: knowledge_base command='sources' "
            "with the page lists every cited passage."
        )
    if brief:
        lines.append(_BRIEF_NOTE)
    return "\n".join(lines)


#: Closes a brief list: what it left out and how to get it.
_BRIEF_NOTE = "(sources='full' adds each document's excerpt, url and read_file id)"


def _page_named_in(record: dict[str, Any], text: str) -> bool:
    """Whether ``record`` is a Knowledge page the result's text already names.

    A section's source is named ``Page › Section``; the text may print the page
    as ``[[Page]] › Section``, so the page's own title counts too.
    """
    if not text or _kind(record) != _PAGE:
        return False
    name = str(record.get("name") or record.get("title") or "").strip()
    if not name:
        return False
    page = name.split(LOCATION_SEPARATOR, 1)[0].strip()
    return name in text or (bool(page) and page in text)


def _brief_line(entry: dict[str, Any]) -> str:
    """``- <path> › <places> · source_ref <ref>``: where it lives and how to cite it.

    With no ``source_ref``, the handle that opens it instead (its ``read_file``
    id, else its url), so every line still names one way to reach the document.
    """
    record = entry["record"]
    location = record.get("location") or {}
    places = entry.get("places") or []
    shown = ", ".join(places[:PLACES_PER_DOCUMENT])
    if len(places) > PLACES_PER_DOCUMENT:
        shown += f" +{len(places) - PLACES_PER_DOCUMENT} more"
    name = str(
        location.get("display_path") or record.get("filename") or record.get("name") or "source"
    )
    head = f"{name}{LOCATION_SEPARATOR}{shown}" if shown else name
    if record.get("source_ref"):
        handle = f"source_ref {record['source_ref']}"
    elif location.get("file_id"):
        handle = f"read_file {location['file_id']}"
    elif location.get("url"):
        handle = str(location["url"])
    else:
        handle = ""
    tail = f" · {handle}" if handle else ""
    return f"- {_line_label(record)}{head}{tail}".rstrip()


#: What each ``approval_state`` of a staged write means for the agent that made it.
_APPROVAL_STATES = {
    "pending": "waiting for review; accept_proposal applies it only when the user asks",
    "auto_apply_queued": "auto-approve is on, so Beakr applies it itself: do not call "
    "accept_proposal",
    "needs_person": "a person must accept it in Beakr; an agent's accept_proposal is refused",
}


def _states_it(text: str) -> bool:
    """Whether the text already says the approval state, in either spelling."""
    lowered = text.lower()
    return "approval_state" in lowered or "approval state:" in lowered


def approval_state_line(state: Any, text: str) -> str:
    """``approval_state: <state> -- <what it means>``, unless the text already says it.

    The engine's write reply carries ``approval_state``; an engine that already
    prints it in the text gets no second copy, and one without it gets no line.
    """
    if not isinstance(state, str) or not state.strip() or _states_it(text):
        return ""
    state = state.strip()
    meaning = _APPROVAL_STATES.get(state)
    return f"approval_state: {state}" + (f" -- {meaning}" if meaning else "")


#: The kinds of source a list names, each with its singular and plural, in the
#: order the count reads. A conversation or a note is not a document: counted as
#: one, and printed with no label, it read as a file with no place (S18-9).
_DOCUMENT = "document"
_PAGE = "Knowledge page"
_KINDS: tuple[tuple[str, str], ...] = (
    (_DOCUMENT, "documents"),
    ("conversation", "conversations"),
    ("agent note", "agent notes"),
    ("note", "notes"),
    (_PAGE, "Knowledge pages"),
)
#: A citation key's source type, for the kinds that are not documents.
_KIND_BY_SOURCE_TYPE = {
    "wiki": _PAGE,
    "conversation": "conversation",
    "agent_note": "agent note",
    "user_note": "note",
}


def _kind(record: dict[str, Any]) -> str:
    """Which kind of source ``record`` is: document, Knowledge page, conversation or note."""
    if str(record.get("source_ref") or "").startswith("beakr-source:v1:wiki:"):
        return _PAGE
    for source_type in (
        str(record.get("source_kind") or "").lower(),
        str(record.get("citation_key") or "").partition(":")[0].lower(),
    ):
        if source_type in _KIND_BY_SOURCE_TYPE:
            return _KIND_BY_SOURCE_TYPE[source_type]
    return _DOCUMENT


def _line_label(record: dict[str, Any]) -> str:
    """``conversation: ``, ``agent note: `` or ``note: `` before a source that is not a file.

    Unlabelled, a conversation's title read as a file with no place in it. A
    file or a page keeps its line as it was: its path leads, so ``<path> ›
    <place>`` stays the one piece a reader opens and cites, and ``read_file``
    or a Knowledge ``source_ref`` already says which it is.
    """
    kind = _kind(record)
    return f"{kind}: " if kind not in (_DOCUMENT, _PAGE) else ""


def _count_label(entries: list[dict[str, Any]]) -> str:
    """``3 documents, 2 conversations, 9 Knowledge pages``: a cat cites more than files."""
    counts = {kind: 0 for kind, _plural in _KINDS}
    for entry in entries:
        counts[_kind(entry["record"])] += 1
    return ", ".join(
        f"{counts[kind]} {kind if counts[kind] == 1 else plural}"
        for kind, plural in _KINDS
        if counts[kind]
    )


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
    tail = f" · {' · '.join(where)}" if where else ""
    head = f"{name}{LOCATION_SEPARATOR}{shown}" if shown else name
    lines = [f"- {_line_label(record)}{head}{tail}".rstrip()]
    if entry["excerpt"]:
        lines.append(f'  "{_one_line(entry["excerpt"], EXCERPT_CHARS)}"')
    return lines
