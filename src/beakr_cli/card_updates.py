"""Changing the cards a project already has: what ``update_existing_cards`` and
``beakr kb types rename-fields|retype`` share.

The stdio ``update_existing_cards`` tool answers the same text as the hosted one
for the same REST answers (beakr-engine
``beakr/api/mcp_remote/card_update_tools.py``), so the refusals, the argument
handling and the two result texts here are that module's, character for
character; the engine's ``tests/unit/test_mcp_every_tool_result_contract.py``
runs both servers on one canned answer and compares. The commands print the
same sentences with their own flag (``--apply``) where the tool's say
``apply=true``: :data:`COMMAND_WORDS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CHANGES = ("rename_fields", "retype")

#: Every refusal the tool answers before it sends anything: ``(message, fix)``.
REFUSALS: dict[str, tuple[str, str]] = {
    "no_type": (
        "Name the card type.",
        "Pass `type`: a type key from knowledge_base command='ontology'.",
    ),
    "bad_type": (
        "A card type's key is letters, digits, spaces, hyphens and underscores.",
        "Pass `type` exactly as knowledge_base command='ontology' prints it.",
    ),
    "no_change": (
        "Say which change to make to the existing cards.",
        "Pass change='rename_fields' with `renames`, or change='retype' with `to_type`.",
    ),
    "no_renames": (
        "Name the fields to rename.",
        "Pass `renames`: a list of {old, new}, the field's current name and its new name.",
    ),
    "no_to_type": (
        "Name the type the cards should become.",
        "Pass `to_type`: a type key from knowledge_base command='ontology'.",
    ),
    "bad_to_type": (
        "A card type's key is letters, digits, spaces, hyphens and underscores.",
        "Pass `to_type` exactly as knowledge_base command='ontology' prints it.",
    ),
}


def renames(value: Any) -> list[dict[str, str]]:
    """``renames`` as the route takes it; a pair missing either name is dropped."""
    if isinstance(value, dict):
        # {old: new} is the same mapping written the short way.
        value = [{"old": old, "new": new} for old, new in value.items()]
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        old, new = str(item.get("old") or "").strip(), str(item.get("new") or "").strip()
        if old and new:
            out.append({"old": old, "new": new})
    return out


# --- The answers. Pure: the engine's, character for character. -------------------------

PREVIEW = "Preview only: nothing was changed."
PREVIEW_NEXT = (
    "Show the person these counts. If they agree, call this again with apply=true to make "
    "the change."
)
UNDO = "It shows in the project's Activity, where a project owner can undo it."
PER_CALL = 500


@dataclass(frozen=True)
class Words:
    """The two places an answer names how to make the change."""

    #: The preview's closing line.
    preview_next: str
    #: How the caller asks for the write, as in "Running this again with ...".
    apply: str


#: The tool's words: the hosted server's.
TOOL_WORDS = Words(preview_next=PREVIEW_NEXT, apply="apply=true")
#: The commands' words: the same sentences, naming the flag.
COMMAND_WORDS = Words(
    preview_next="Run this again with --apply to make the change.", apply="--apply"
)


def _n(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _pairs(mapping: Any) -> str:
    """``{old: new}`` as "old to new; old to new", in the order given."""
    if not isinstance(mapping, dict):
        return ""
    return "; ".join(f"{old} to {new}" for old, new in mapping.items())


def _undo_line(result: dict[str, Any]) -> str:
    if result.get("undo_event_id"):
        return UNDO
    return f"A project owner can undo it by reverting change {result.get('commit_id')}."


def fields_renamed_text(result: dict[str, Any], words: Words = TOOL_WORDS) -> str:
    """What a field rename would change or changed, from the route's answer."""
    page_type = result.get("page_type")
    cards, changed = _n(result.get("cards")), _n(result.get("cards_changed"))
    written, remaining = _n(result.get("cards_written")), _n(result.get("cards_remaining"))
    applied, refused = _pairs(result.get("applied")), _pairs(result.get("refused"))
    lines: list[str] = []
    if result.get("dry_run", True):
        lines.append(PREVIEW)
        if applied and changed:
            lines.append(
                f"Renaming {applied} would change {changed} of "
                f"{_count(cards, f'{page_type} card')}: "
                f"{_count(_n(result.get('rows_renamed')), 'row')} renamed, "
                f"{_count(_n(result.get('rows_merged')), 'row')} merged into a row that becomes "
                "the same field."
            )
        else:
            lines.append(f"No {page_type} card would change ({_count(cards, 'card')} read).")
        if refused:
            lines.append(f"Not accepted by the safety checks, so these would stay: {refused}.")
        if remaining:
            lines.append(
                f"One call changes at most {PER_CALL} cards: {_count(remaining, 'card')} would be "
                "left for the next call."
            )
        if applied and changed:
            lines.append(words.preview_next)
        return "\n".join(lines)
    if written:
        lines.append(f"Renamed {applied} on {_count(written, f'{page_type} card')}.")
    else:
        lines.append(f"No {page_type} card was changed.")
    if refused:
        lines.append(f"Not accepted by the safety checks, so these stay: {refused}.")
    conflicted, held = _n(result.get("cards_conflicted")), _n(result.get("cards_refused"))
    if conflicted:
        lines.append(
            f"{_count(conflicted, 'card')} changed while this ran and "
            f"{'was' if conflicted == 1 else 'were'} left as "
            f"{'it was' if conflicted == 1 else 'they were'}."
        )
    if held:
        lines.append(
            f"{_count(held, 'card')} could not be rewritten safely and "
            f"{'waits' if held == 1 else 'wait'} for a person."
        )
    if remaining:
        lines.append(
            f"{_count(remaining, 'card')} still "
            f"{'needs' if remaining == 1 else 'need'} the change. Running this again with "
            f"{words.apply} picks up the cards that were skipped."
        )
    if written:
        lines.append(_undo_line(result))
    return "\n".join(lines)


def cards_retyped_text(result: dict[str, Any], words: Words = TOOL_WORDS) -> str:
    """What a retype would move or moved, from the route's answer."""
    source, target = result.get("from_type"), result.get("to_type")
    cards, remaining = _n(result.get("cards")), _n(result.get("cards_remaining"))
    lines: list[str] = []
    if result.get("dry_run", True):
        lines.append(PREVIEW)
        if not cards:
            lines.append(f"This project has no {source} cards to move.")
            return "\n".join(lines)
        lines.append(f"{_count(cards, f'{source} card')} would become {target} cards.")
        if remaining:
            lines.append(
                f"One call moves at most {PER_CALL} cards: {_count(remaining, 'card')} would be "
                "left for the next call."
            )
        lines.append(
            f"A card's text is not changed, so its rows keep their field names. If {target} "
            f"names a field differently, rename fields on {target} afterwards."
        )
        lines.append(words.preview_next)
        return "\n".join(lines)
    moved, skipped = _n(result.get("cards_retyped")), _n(result.get("cards_skipped"))
    if moved:
        lines.append(f"Moved {_count(moved, f'{source} card')} to {target}.")
    else:
        lines.append(f"No {source} card was moved.")
    if skipped:
        lines.append(
            f"{_count(skipped, 'card')} {'was' if skipped == 1 else 'were'} being changed by "
            f"something else and {'was' if skipped == 1 else 'were'} skipped."
        )
    if remaining:
        lines.append(
            f"{_count(remaining, f'{source} card')} "
            f"{'is' if remaining == 1 else 'are'} left. Running this again with {words.apply} "
            "picks up the cards that were skipped."
        )
    if moved:
        lines.append(_undo_line(result))
    return "\n".join(lines)
