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
  (``memo.pdf › p. 7``). A read chooses how much of it to show with its
  ``sources`` argument (:data:`SOURCE_MODES`); see :func:`sources_block`;
- for a write, its ``approval_state`` and what it means, unless the text
  already says it (:func:`approval_state_line`);
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

#: How much of the Sources list a read shows, its ``sources`` argument:
#: ``none`` leaves it out, ``brief`` is one line per document with no excerpt
#: (Knowledge pages the text already names left out), ``full`` the whole list.
#: beakr-cli declares the same values and description on its stdio tools.
SOURCE_MODES: tuple[str, ...] = ("none", "brief", "full")

#: The ``sources`` argument's description in both servers' input schemas.
SOURCE_MODE_DESCRIPTION = (
    "The Sources list closing the result. brief (default): one line per cited "
    "document, its path, cited places and source_ref, leaving out Knowledge pages "
    "the text already names. full: every source with an excerpt, url and read_file "
    "id. none: no list."
)

#: What the read tools show when not told. The full list closed every Knowledge
#: result with up to 8,000 characters, most of it excerpts of passages the text
#: had already quoted and Knowledge pages a listing had already printed.
DEFAULT_SOURCE_MODE = "brief"

#: The result key a read sets to its ``sources`` argument. A result without it
#: gets the full list.
SOURCE_MODE_KEY = "source_listing"

#: The engine's step after a read that has no step of its own. Equals
#: ``kb_result_coverage._OUTSIDE_DEFAULT_NEXT``; repeated for the same standalone
#: reason as ``UPLOAD_PROVIDER``.
CITE_HINT = "Cite what you use with its readable reference (memo.pdf \u203a p. 7)."

#: Control, bidi and zero-width characters, shown as ``\\uXXXX`` in every text
#: this module returns. Equals ``beakr.core.visible_controls`` (which has the
#: root cause and why the escape is JSON's); repeated for the same standalone
#: reason as ``UPLOAD_PROVIDER``. A carriage return in a ``\\r\\n`` line ending
#: stays. The ``Coverage:`` line is JSON, so its escaped values parse back exact.
_HIDDEN_CHARACTERS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2060\u2066-\u2069\ufeff]"
    "|\r(?!\n)"
)


def visible_controls(text: str) -> str:
    """``text`` with each control, bidi and zero-width character as ``\\uXXXX``."""
    return _HIDDEN_CHARACTERS.sub(lambda match: f"\\u{ord(match.group(0)):04x}", text)


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
    if tool in _SOURCE_LISTING_TOOLS and mode == "none":
        # The list was left out on request, so a step that says to cite from it
        # cannot be followed (agent tester report, 2026-11-13). Nothing is said
        # in its place: ``none`` asks for the shortest reply, and the tool's
        # ``sources`` argument already says how to get the list.
        hints = [hint for hint in hints if hint != CITE_HINT]
    if hints:
        parts.append("\n".join(f"next: {hint}" for hint in hints))
    return visible_controls("\n\n".join(parts)) if parts else "(no output)"


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
    # JSON already escapes everything but DEL, which ``visible_controls`` shows.
    return visible_controls(json.dumps(compact, separators=(", ", ": ")))


def body_error_text(body: dict[str, Any]) -> str:
    """:func:`error_text` for a tool result whose ``status`` is ``error``."""
    return error_text(
        message=str(body.get("message") or body.get("content") or ""),
        error_code=body.get("error_code"),
        fix=body.get("fix"),
        hints=[str(hint) for hint in body.get("next") or []],
        request_id=body.get("request_id"),
    )


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


#: The document a citation key belongs to: ``unit:<id>-u-m2`` and ``unit:<id>-u-m5``
#: are passages of ``unit:<id>``. The rule of ``readable_citations._DOCUMENT_KEY``,
#: restated because this module loads standalone.
_KEY_DOCUMENT = re.compile(
    r"^[a-z_]+:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
#: The first eight characters of that id, for telling same-named sources apart.
_KEY_SHORT_ID = re.compile(r"^[a-z_]+:([0-9a-fA-F]{8})-")


def _document_key(record: dict[str, Any]) -> str:
    """What makes two records one source: its identity, never its display name.

    A file is its file id. Anything else is the document its citation key
    names, so two different call notes that are both titled "Granola: Albert"
    stay two entries: keyed by the name they share, they were one line (agent
    tester report, 2026-11-13). A Knowledge section keeps its own line, as
    before. A record with neither falls back to where it lives, then its name.
    """
    location = record.get("location") or {}
    if location.get("file_id"):
        return str(location["file_id"])
    key = str(record.get("citation_key") or "")
    document = _KEY_DOCUMENT.match(key)
    if document and _kind(record) != _PAGE:
        return document.group(0).lower()
    return str(
        location.get("url")
        or location.get("display_path")
        or record.get("filename")
        or record.get("name")
        or key
        or id(record)
    )


def _display_name(record: dict[str, Any]) -> str:
    """The name a Sources line leads with: where the source lives, else its name."""
    location = record.get("location") or {}
    return str(
        location.get("display_path") or record.get("filename") or record.get("name") or "source"
    )


def _label_same_named(entries: list[dict[str, Any]]) -> None:
    """Give each of several sources that share a name the label that tells it apart.

    The name the result's text uses for it when the record carries one
    (``reference``, set by the engine for exactly this case), else the
    source's own date, else a short id read off its citation key.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_name.setdefault(_display_name(entry["record"]).casefold(), []).append(entry)
    for clash in by_name.values():
        if len(clash) < 2:
            continue
        dates = [
            str((entry["record"].get("evidence") or {}).get("source_recorded_at") or "")[:10]
            for entry in clash
        ]
        # A date only when it tells every one of them apart, as the engine names them.
        dated = len(set(dates)) == len(dates) and all(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) for date in dates
        )
        for entry, date in zip(clash, dates, strict=True):
            record = entry["record"]
            name = _display_name(record)
            reference = str(record.get("reference") or "").split(LOCATION_SEPARATOR, 1)[0]
            short = _KEY_SHORT_ID.match(str(record.get("citation_key") or ""))
            if reference and reference != name and _kind(record) != _PAGE:
                entry["label"] = reference
            elif dated:
                entry["label"] = f"{name} \u00b7 {date}"
            elif short:
                entry["label"] = f"{name} \u00b7 id {short.group(1).lower()}"


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
    (the default) is the whole list.
    """
    documents: dict[str, dict[str, Any]] = {}
    for record in sources:
        if not isinstance(record, dict):
            continue
        entry = documents.setdefault(
            _document_key(record),
            {"record": record, "places": [], "excerpt": None, "excerpt_authored": False},
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
        evidence = record.get("evidence") or {}
        # The document's one excerpt is a passage of it where one was cited: the
        # compiler's own analysis (authored evidence on a scientific file) is
        # shown only when nothing else was, and labelled as what it is.
        for excerpt, authored in [
            (evidence.get("excerpt"), _authored(evidence)),
            *((excerpt.get("text"), _authored(excerpt)) for excerpt in excerpts),
        ]:
            if excerpt and (
                entry["excerpt"] is None or (entry["excerpt_authored"] and not authored)
            ):
                entry["excerpt"] = excerpt
                entry["excerpt_authored"] = authored
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
    _label_same_named(entries)
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
    name = entry.get("label") or _display_name(record)
    head = f"{name}{LOCATION_SEPARATOR}{shown}" if shown else name
    if record.get("source_ref"):
        handle = f"source_ref {record['source_ref']}"
    elif location.get("file_id"):
        handle = f"read_file {location['file_id']}"
    elif location.get("url"):
        handle = str(location["url"])
    else:
        handle = ""
    tail = f" \u00b7 {handle}" if handle else ""
    read_call = _read_call(record)
    if read_call:
        tail += f" \u00b7 {read_call}"
    return f"- {_line_label(record)}{head}{tail}".rstrip()


def _read_call(record: dict[str, Any]) -> str:
    """``read with integrations command='call' tool='granola_get_note' ...``, or "".

    A connected item that is not a file (a meeting note, an email or Slack
    thread) is read through its provider's own tool with the provider's own
    id. Its share link carries a different id that the tool refuses, so the
    line names the exact call (``native_source_fetch.public_native_call``).
    """
    retrieval = record.get("retrieval")
    if not isinstance(retrieval, dict):
        return ""
    arguments = retrieval.get("arguments")
    if not isinstance(arguments, dict) or "handle" in arguments or not arguments:
        return ""
    return (
        f"read with integrations command='call' tool='{retrieval.get('tool')}' "
        f"arguments={json.dumps(arguments, sort_keys=True)}"
    )


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

    The engine's write reply carries ``approval_state``; a reply whose text
    already prints it gets no second copy, and one without it gets no line.
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
#: A record in a system outside Beakr, cited by its link and never opened.
_RECORD = "record"
_KINDS: tuple[tuple[str, str], ...] = (
    (_DOCUMENT, "documents"),
    (_RECORD, "records"),
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
    "external_record": _RECORD,
    "record": _RECORD,
}


def _kind(record: dict[str, Any]) -> str:
    """Which kind of source ``record`` is: a document, a Knowledge page, a conversation or a note."""
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
    outside_record = _kind(record) == _RECORD
    shown = ", ".join(places[:PLACES_PER_DOCUMENT])
    if len(places) > PLACES_PER_DOCUMENT:
        shown += f" +{len(places) - PLACES_PER_DOCUMENT} more"
    # Where the source lives: a Beakr file's drive path, a connector file's path
    # in its service, or a connector item's place there.
    # An outside record is named as the text cites it, ``CDD Vault › MOL-1234``;
    # several sources that share a name carry the label that tells them apart.
    name = entry.get("label") or str(
        location.get("display_path")
        or record.get("filename")
        or (record.get("reference") if outside_record else None)
        or record.get("name")
        or "source"
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
    read_call = _read_call(record)
    if read_call:
        where.append(read_call)
    separator = " \u00b7 "
    tail = separator + separator.join(where) if where else ""
    head = f"{name}{LOCATION_SEPARATOR}{shown}" if shown else name
    lines = [f"- {_line_label(record)}{head}{tail}".rstrip()]
    if entry["excerpt"]:
        if entry.get("excerpt_authored"):
            label = " (paraphrase)"
        elif outside_record:
            # Its writer's words for a record Beakr never opens.
            label = " (unverified)"
        else:
            label = ""
        lines.append(f'  "{_one_line(entry["excerpt"], EXCERPT_CHARS)}"{label}')
    return lines


def _authored(evidence: dict[str, Any]) -> bool:
    """Whether an excerpt is the compiler's wording rather than the document's.

    The rule of ``beakr.services.evidence.is_authored_excerpt``, restated because
    this module loads standalone and imports nothing from beakr.
    """
    origin = str(evidence.get("quote_origin") or "").strip().lower()
    kind = str(evidence.get("evidence_kind") or "").strip().lower()
    return bool(evidence.get("authored")) or origin == "authored" or kind == "description"
