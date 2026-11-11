"""A wiki proposal's payload as a person reads it at a terminal.

``beakr kb proposals show`` (and the review step of ``accept``) used to dump the
payload as indented JSON: an edit arrived as two full copies of the page with
``\\n`` escapes, and the content hash a reviewer must copy was printed above it
and scrolled away. Here a content change is a unified diff, page text is shown
as text, and every other field is a labelled line. ``--json`` still prints the
payload unchanged.
"""

from __future__ import annotations

import difflib
from typing import Any

#: One rendered line and its Rich style (None for plain text).
Line = tuple[str, str | None]

#: Top-level payload keys the proposal header already shows, or bookkeeping a
#: reviewer does not act on.
_HEADER_KEYS = frozenset({"type", "summary", "page_title", "group_id", "project_id"})

#: Keys rendered as part of a content change rather than as their own lines.
_CHANGE_KEYS = frozenset({"current_content", "proposed_content", "current_title", "proposed_title"})

#: Lines of unchanged text kept around each changed hunk.
_DIFF_CONTEXT = 2

_DIFF_STYLES = {"+": "green", "-": "red", "@": "cyan"}


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_change(item: dict[str, Any]) -> bool:
    return isinstance(item.get("current_content"), str) and isinstance(
        item.get("proposed_content"), str
    )


def _diff_lines(current: str, proposed: str, indent: str) -> list[Line]:
    lines: list[Line] = []
    diff = difflib.unified_diff(
        current.splitlines(), proposed.splitlines(), lineterm="", n=_DIFF_CONTEXT
    )
    for line in diff:
        if line.startswith(("---", "+++")):
            continue  # file headers: there are no files here
        lines.append((f"{indent}{line}", _DIFF_STYLES.get(line[:1])))
    return lines or [(f"{indent}(no text change)", "dim")]


def _change_lines(item: dict[str, Any], indent: str) -> list[Line]:
    lines: list[Line] = []
    old_title, new_title = item.get("current_title"), item.get("proposed_title")
    if new_title and new_title != old_title:
        lines.append((f"{indent}Title: {old_title} -> {new_title}", None))
    lines.append((f"{indent}Changes:", "bold"))
    lines.extend(_diff_lines(item["current_content"], item["proposed_content"], indent + "  "))
    return lines


def _text_lines(text: str, indent: str) -> list[Line]:
    return [(f"{indent}{line}", None) for line in text.splitlines()] or [(indent, None)]


def _field_lines(key: str, value: Any, indent: str) -> list[Line]:
    label = _label(key)
    if isinstance(value, dict):
        return [(f"{indent}{label}:", "bold"), *_object_lines(value, indent + "  ")]
    if isinstance(value, list):
        lines: list[Line] = [(f"{indent}{label}:", "bold")]
        for entry in value:
            if isinstance(entry, dict):
                body = _object_lines(entry, indent + "    ")
                if body:
                    first, style = body[0]
                    lines.append((f"{indent}  - {first.lstrip()}", style))
                    lines.extend(body[1:])
            else:
                lines.append((f"{indent}  - {entry}", None))
        return lines
    if isinstance(value, bool):
        return [(f"{indent}{label}: {'yes' if value else 'no'}", None)]
    text = str(value)
    if "\n" in text:
        return [(f"{indent}{label}:", "bold"), *_text_lines(text, indent + "  ")]
    return [(f"{indent}{label}: {text}", None)]


def _object_lines(
    item: dict[str, Any], indent: str, *, skip: frozenset[str] = frozenset()
) -> list[Line]:
    lines: list[Line] = []
    change = _is_change(item)
    for key, value in item.items():
        if key in skip or _empty(value) or (change and key in _CHANGE_KEYS):
            continue
        lines.extend(_field_lines(key, value, indent))
    if change:
        lines.extend(_change_lines(item, indent))
    return lines


def payload_lines(payload: dict[str, Any]) -> list[Line]:
    """The payload's fields, a content change last as a diff, for a terminal."""
    return _object_lines(payload, "", skip=_HEADER_KEYS)


def filed_by_line(proposal: dict[str, Any]) -> str | None:
    """Who filed the proposal, e.g. ``Filed by Dana Reyes via claude-code/2.1``.

    The engine's ``filed_by.text`` names the person and the agent (else the
    client) that filed it; an engine without it gives no line.
    """
    filed_by = proposal.get("filed_by")
    text = filed_by.get("text") if isinstance(filed_by, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    return text[:1].upper() + text[1:]


def staged_proposal_lines(result: dict[str, Any]) -> list[str]:
    """What a write that staged a proposal adds below its text: the content hash, and
    whether the project's cross-project guard held it for its owners.

    The text already names the proposal id; ``kb proposals accept --reviewed-hash``
    takes this hash back, as the MCP servers print it (``mcp_render.render``).
    """
    proposal = result.get("proposal") if isinstance(result, dict) else None
    lines: list[str] = []
    if isinstance(proposal, dict) and proposal.get("content_hash"):
        lines.append(f"content_hash: {proposal['content_hash']}")
    held = held_line(result) if isinstance(result, dict) else None
    if held and not says_held(_text_of(result)):
        lines.append(held)
    return lines


def _text_of(result: dict[str, Any]) -> str:
    text = result.get("text") or result.get("content") or ""
    return text if isinstance(text, str) else ""


def cross_project_hold(holder: dict[str, Any]) -> dict[str, Any] | None:
    """The proposal's ``cross_project_hold`` (the engine's project isolation), when held."""
    for found in (holder, holder.get("proposal"), holder.get("outcome")):
        hold = found.get("cross_project_hold") if isinstance(found, dict) else None
        if isinstance(hold, dict):
            return hold
    return None


def says_held(text: str) -> bool:
    """Whether a write's reply text already says it was held (the engine's own line,
    "Held for an owner or admin of Pharma B to accept: ...")."""
    return "held for" in text.lower()


def _via(project: dict[str, Any]) -> str:
    """How another project's content came in: ``cited``, ``read in this session``."""
    return ", ".join(str(v).replace("_", " ") for v in project.get("via") or [])


def held_line(holder: dict[str, Any]) -> str | None:
    """``Held for approval by an owner or admin of Pharma B: <why>``, for a held proposal.

    The project written guards against content from other projects (its
    ``cross_project_guard`` is ``hold``): the proposal was staged as usual, but only
    an owner or admin of that project can accept it, and auto-approve never does.
    """
    hold = cross_project_hold(holder)
    if hold is None:
        return None
    project = hold.get("project") if isinstance(hold.get("project"), dict) else {}
    name = project.get("name") or project.get("id") or "the project it writes"
    reason = str(hold.get("reason") or "").strip()
    if not reason:
        others = [
            f"{p.get('name') or p.get('id')} ({_via(p)})"
            for p in hold.get("projects") or []
            if isinstance(p, dict)
        ]
        reason = f"Content from {', '.join(others)}." if others else ""
    reason = reason or "It brings in content from another project."
    return (
        f"Held for approval by an owner or admin of {name}. {reason} Only an owner or admin of "
        f"{name} can accept it: auto-approve never does, and an agent's accept is refused."
    )


#: show_proposal's line for a proposal only a person may accept: the engine's
#: ``proposal_text.NEEDS_A_PERSON_LINE``, which the hosted server prints too.
NEEDS_A_PERSON_LINE = (
    "Needs a person to accept: this project requires a person to accept proposals. "
    "Ask the user to accept it in Beakr; an agent's accept_proposal is refused."
)


def decided_line(proposal: dict[str, Any]) -> str | None:
    """``Decided: accepted by Dana Reyes at <time>``, for a proposal already decided.

    The engine's proposal read and its repeated accept name who decided it
    (``reviewed_by_name``, a person, or None), when (``reviewed_at``) and whether
    auto-approve applied it (``auto_approved``). An engine without these fields,
    or a pending proposal, gives no line.
    """
    at = proposal.get("reviewed_at")
    by = proposal.get("reviewed_by_name")
    auto = proposal.get("auto_approved") is True
    if not (at or by or auto):
        return None
    when = f" at {at}" if at else ""
    if auto:
        return f"Decided: applied by auto-approve{when}"
    status = str(proposal.get("status") or "decided")
    who = f" by {by}" if by else ""
    return f"Decided: {status}{who}{when}"


#: A citation's quoted text in a proposal summary, before it is cut.
_QUOTE_CHARS = 160


def _short(value: Any, width: int = _QUOTE_CHARS) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def _proposed_sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The sections a payload writes: ``sections``, or the one an edit_section names."""
    sections = [item for item in payload.get("sections") or [] if isinstance(item, dict)]
    if sections:
        return sections
    if payload.get("section_id"):
        return [
            {
                "id": payload["section_id"],
                "title": payload.get("section_title"),
                "citations": payload.get("citations") or [],
            }
        ]
    return []


def _section_label(section: dict[str, Any]) -> str:
    ident, title = section.get("id"), section.get("title")
    if ident and title:
        return f"{title} ({ident})"
    return str(title or ident or "(untitled section)")


def _citation_line(citation: dict[str, Any]) -> str:
    """``- <key> · <stance> · "<quote>"``, the excerpt only when it says something else."""
    meta = citation.get("meta") if isinstance(citation.get("meta"), dict) else {}
    key = (
        citation.get("key")
        or citation.get("source_ref")
        or citation.get("reference")
        or meta.get("citation_key")
        or "source"
    )
    parts = [str(key)]
    if citation.get("source_title"):
        parts.append(_short(citation["source_title"], 80))
    if citation.get("stance"):
        parts.append(str(citation["stance"]))
    quote = meta.get("quote") or citation.get("quote")
    excerpt = meta.get("excerpt") or citation.get("excerpt")
    if quote:
        parts.append(f'"{_short(quote)}"')
    if excerpt and " ".join(str(excerpt).split()) != " ".join(str(quote or "").split()):
        parts.append(f'excerpt: "{_short(excerpt)}"')
    return "  - " + " · ".join(parts)


def proposal_summary_lines(payload: dict[str, Any]) -> list[str]:
    """What a proposal changes, without its payload: sections, then one line per citation.

    The stdio ``show_proposal`` used to append the whole payload as indented
    JSON: page text twice (current and proposed), and every conversation
    citation with the same text in ``meta.quote`` and ``meta.excerpt`` plus its
    own ``filed_by`` block. The filer is printed once, above; the payload stays
    one ``raw=true`` away.
    """
    lines: list[str] = []
    sections = _proposed_sections(payload)
    if sections:
        lines.append(f"Sections changed ({len(sections)}):")
        lines.extend(f"  - {_section_label(section)}" for section in sections)
    patches = [item for item in payload.get("patches") or [] if isinstance(item, dict)]
    if patches:
        lines.append(f"Patches ({len(patches)}):")
        for patch in patches:
            target = patch.get("section") or patch.get("find") or ""
            shown = f" {_short(target, 80)}" if target else ""
            lines.append(f"  - {patch.get('op') or 'patch'}{shown}")
    citations = [
        citation
        for section in sections
        for citation in section.get("citations") or []
        if isinstance(citation, dict)
    ]
    if not sections:
        citations = [item for item in payload.get("citations") or [] if isinstance(item, dict)]
    if citations:
        lines.append(f"Citations ({len(citations)}):")
        lines.extend(_citation_line(citation) for citation in citations)
    return lines
