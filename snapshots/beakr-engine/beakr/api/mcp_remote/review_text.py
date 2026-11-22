"""What ``review_options`` and ``resolve_review`` print, and how an option is chosen.

A review flag (a ``review`` proposal) marks a page section where Knowledge found a
concern it could not settle. ``review_options`` reads the flag
(``GET .../conflict``) and Beakr's drafted resolutions (``POST .../resolutions``)
and prints each option with what it changes; ``resolve_review`` applies one. An
option is applied by the route that fits it: a card-table option (it carries a
``row_operation``) with ``POST .../resolve-rows``, any other as a section rewrite
with ``POST .../resolve``. Both are pinned to the revision the options were
drafted against.

The ``beakr-cli`` stdio server and ``beakr kb review`` print the same text from
their own copy (``beakr_cli/review_flags.py``). Like ``render``, this module
imports nothing from ``beakr``.
"""

from __future__ import annotations

from typing import Any

#: How much of an option's new section text is shown when it has no targeted edits.
_BODY_CHARS = 1200
#: How much of one targeted edit's old or new text is shown.
_EDIT_CHARS = 300


def _short(value: Any, width: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def is_row_option(option: dict[str, Any]) -> bool:
    """Whether the option is a row operation on a card table (applied with resolve-rows)."""
    return isinstance(option.get("row_operation"), dict)


def find_option(options: list[dict[str, Any]], option_id: str) -> dict[str, Any] | None:
    """The drafted option by its id (A, B, ...) or its 1-based number, else None."""
    wanted = str(option_id or "").strip()
    for option in options:
        if str(option.get("id") or "").strip().casefold() == wanted.casefold():
            return option
    if wanted.isdigit() and 1 <= int(wanted) <= len(options):
        return options[int(wanted) - 1]
    return None


def _row_lines(option: dict[str, Any]) -> list[str]:
    operation = option.get("row_operation") or {}
    lines = [
        f"   Row change: {operation.get('kind')} on {operation.get('table')} "
        f"(rows {', '.join(operation.get('row_ids') or []) or 'new'})"
    ]
    row = operation.get("row")
    if isinstance(row, dict):
        cells = ", ".join(f"{key}={value}" for key, value in row.items() if key != "evidence")
        if cells:
            lines.append(f"   New row: {_short(cells, _EDIT_CHARS)}")
    if operation.get("reason"):
        lines.append(f"   Reason: {_short(operation['reason'], _EDIT_CHARS)}")
    return lines


def _change_lines(option: dict[str, Any]) -> list[str]:
    """What an option changes: its row operation, its edits, or its new section text."""
    if is_row_option(option):
        return _row_lines(option)
    edits = option.get("edits")
    if isinstance(edits, list) and edits:
        lines = []
        for edit in edits:
            lines.append(f"   - replace: {_short(edit.get('old_text'), _EDIT_CHARS)}")
            new_text = str(edit.get("new_text") or "")
            lines.append(
                f"     with:    {_short(new_text, _EDIT_CHARS)}" if new_text else "     (deleted)"
            )
        return lines
    body = _short(option.get("new_section_body"), _BODY_CHARS)
    return ["   New section text:", *(f"     {line}" for line in body.splitlines())]


def options_text(conflict: dict[str, Any], drafted: dict[str, Any]) -> str:
    """The flag and its drafted options, numbered, with how to apply one."""
    proposal_id = drafted.get("proposal_id") or conflict.get("proposal_id")
    revision = drafted.get("current_revision", conflict.get("current_revision"))
    lines = [f"Review flag {proposal_id}"]
    if conflict.get("page_title"):
        lines.append(f"Page: {conflict['page_title']}")
    section = conflict.get("section_title") or conflict.get("section_id")
    if section:
        lines.append(f"Section: {section}")
    if conflict.get("reason"):
        lines.append(f"Why flagged: {conflict['reason']}")
    if conflict.get("is_stale"):
        lines.append("The flagged text changed since it was flagged; the concern may be settled.")
    lines.append(f"Page revision: {revision}")
    options = drafted.get("options") or []
    if not options:
        reason = drafted.get("no_draft_reason") or "no options"
        lines.append(f"No drafted options ({reason}). Resolve it with your own section text.")
        return "\n".join(lines)
    lines.append("")
    for number, option in enumerate(options, start=1):
        label = option.get("label") or ""
        lines.append(f"{number}. Option {option.get('id')}: {label}".rstrip(": "))
        if option.get("summary"):
            lines.append(f"   {option['summary']}")
        lines.extend(_change_lines(option))
    lines.append("")
    lines.append(
        f"Apply one with resolve_review(proposal_id='{proposal_id}', option_id='<id>', "
        f"expected_revision={revision}), only after the user chooses it."
    )
    return "\n".join(lines)


def resolved_text(result: dict[str, Any]) -> str:
    """The answer of resolve, resolve-rows or resolve-cards, in one or a few lines."""
    proposal_id = result.get("proposal_id")
    if "updated" in result:
        updated = result.get("updated") or []
        skipped = result.get("skipped") or []
        if result.get("status") != "resolved":
            head = f"Review {proposal_id} is still pending: no card changed."
        else:
            head = f"Resolved review {proposal_id} on {len(updated)} card(s)."
        lines = [head]
        for card in updated:
            lines.append(f"Updated: {card.get('page_title') or card.get('page_id')}")
        for card in skipped:
            name = card.get("page_title") or card.get("page_id") or "a card you cannot see"
            lines.append(f"Skipped: {name} ({card.get('reason')})")
        return "\n".join(lines)
    return (
        f"Resolved review flag {proposal_id}: section {result.get('section_id')} changed; "
        f"the page is now at revision {result.get('new_revision')}."
    )
