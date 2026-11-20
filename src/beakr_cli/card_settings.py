"""A project's card type settings: what ``card_type_settings``, ``create_project`` and
``beakr kb types settings`` share.

The stdio tools answer the same text as the hosted ones for the same REST
answers (beakr-engine ``beakr/api/mcp_remote/card_settings_tools.py`` and the
``card_types`` part of ``project_tools.py``), so the refusals and result texts
here are those modules', character for character; the engine's
``tests/unit/test_mcp_every_tool_result_contract.py`` runs both servers on one
canned answer and compares. The command prints the same sentences with its own
flags where the tool's name its arguments: :data:`COMMAND_WORDS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

INHERIT_VALUES = ("organization", "builtin", "own")

#: Every refusal ``card_type_settings`` answers before it sends anything: ``(message, fix)``.
REFUSALS: dict[str, tuple[str, str]] = {
    "no_change": (
        "Say which setting to change.",
        "Pass inherit ('organization', 'builtin' or 'own'), agent_may_change (true or "
        "false), or both.",
    ),
    "bad_inherit": (
        "inherit is one of 'organization', 'builtin' or 'own'.",
        "Pass inherit='organization' (the organization's and the built-in types), "
        "'builtin' (the built-in types only) or 'own' (only the project's own types).",
    ),
    "bad_agent_may_change": (
        "agent_may_change is true or false.",
        "Pass agent_may_change=false so only people change the project's card types, or "
        "true to let Beakr add and change them.",
    ),
}

#: What ``create_project`` refuses in its ``card_types``: ``(message, fix)``.
CREATE_REFUSALS: dict[str, tuple[str, str]] = {
    "bad_inherit": (
        "card_types.inherit is one of 'organization', 'builtin' or 'own'.",
        "Pass 'organization' (the organization's and the built-in types), 'builtin' "
        "(the built-in types only) or 'own' (only the project's own types).",
    ),
    "own_without_types": (
        "A project that uses only its own card types needs at least one to start with.",
        "Pass card_types.copy_types_from (a project whose own card types to copy), or "
        "create the project without inherit='own', define its types, then switch with "
        "card_type_settings.",
    ),
}

# --- The answers. Pure: the engine's, character for character. -------------------------

PREVIEW = "Preview only: nothing was changed."
PREVIEW_NEXT = (
    "Show the person which card types would drop out and how many cards each has. If they "
    "agree, call this again with apply=true to make the change."
)
PREVIEW_NEXT_SETTING = (
    "Show the person this. If they agree, call this again with apply=true to make the change."
)
UNCHANGED = "The project's card type settings already say this; nothing was changed."
CARDS_STAY = "Their cards are not changed and stay readable; switching back offers the types again."
KEEP_HINT = (
    "To keep the types that have cards as the project's own types, pass keep_types_in_use=true."
)
KEPT_PAGE_TYPES = "It keeps these built-in types: {keys}."
RECORDED = "It shows in the project's Activity and in its card type history."
#: What a choice of ``inherit`` reads as, in a settings answer and a created project's.
SOURCES = {
    "organization": "the organization's card types and the built-in ones",
    "builtin": "the built-in card types only",
    "own": "only its own card types",
}


@dataclass(frozen=True)
class Words:
    """The places a settings answer names how to make the change."""

    #: The preview's closing line when the offered types change.
    preview_next: str
    #: The preview's closing line when only who may change them does.
    preview_next_setting: str
    #: How to keep the dropped types that have cards.
    keep_hint: str


#: The tool's words: the hosted server's.
TOOL_WORDS = Words(
    preview_next=PREVIEW_NEXT, preview_next_setting=PREVIEW_NEXT_SETTING, keep_hint=KEEP_HINT
)
#: The command's words: the same sentences, naming its flags.
COMMAND_WORDS = Words(
    preview_next="Run this again with --apply to make the change.",
    preview_next_setting="Run this again with --apply to make the change.",
    keep_hint="To keep the types that have cards as the project's own types, pass "
    "--keep-types-in-use.",
)


def _n(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _types(items: Any) -> str:
    """``Label (key, 12 cards); Label (key, no cards)``, in the order given."""
    described = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        cards = _n(item.get("live_cards"))
        described.append(
            f"{item.get('label') or item.get('key')} ({item.get('key')}, "
            f"{_count(cards, 'card') if cards else 'no cards'})"
        )
    return "; ".join(described)


def _settings(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _agent_line(may_change: bool, *, preview: bool) -> str:
    if may_change:
        return (
            "Beakr would be able to add and change this project's card types."
            if preview
            else "Beakr can add and change this project's card types."
        )
    return (
        "Beakr would no longer add or change this project's card types; only people would."
        if preview
        else "Beakr no longer adds or changes this project's card types; only people do."
    )


def settings_text(result: dict[str, Any], words: Words = TOOL_WORDS) -> str:
    """What a settings change would do or did, from the route's answer."""
    current, requested = _settings(result.get("current")), _settings(result.get("requested"))
    preview = bool(result.get("dry_run", True))
    dropped, kept = _types(result.get("types_dropped")), _types(result.get("types_kept"))
    sources_change = current.get("inherit") != requested.get("inherit")
    agent_change = current.get("agent_may_change") != requested.get("agent_may_change")
    if not sources_change and not agent_change:
        return UNCHANGED
    lines: list[str] = [PREVIEW] if preview else []
    if sources_change:
        source = SOURCES.get(str(requested.get("inherit")), str(requested.get("inherit")))
        lines.append(
            f"This project would be offered {source}."
            if preview
            else f"This project is now offered {source}."
        )
        kept_page_types = [str(key) for key in result.get("kept_types") or []]
        if kept_page_types:
            lines.append(KEPT_PAGE_TYPES.format(keys=", ".join(kept_page_types)))
        if kept:
            lines.append(
                f"{'Would become' if preview else 'Now'} the project's own card types, because "
                f"cards carry them: {kept}."
            )
        if dropped:
            lines.append(
                f"{'Would no longer be' if preview else 'No longer'} offered in this project: "
                f"{dropped}."
            )
            lines.append(CARDS_STAY)
            if (
                preview
                and not kept
                and any(
                    _n(item.get("live_cards"))
                    for item in result.get("types_dropped") or []
                    if isinstance(item, dict)
                )
            ):
                lines.append(words.keep_hint)
        else:
            lines.append(
                "No card type would drop out of this project."
                if preview
                else "No card type dropped out of this project."
            )
    if agent_change:
        lines.append(_agent_line(bool(requested.get("agent_may_change")), preview=preview))
    if preview:
        lines.append(words.preview_next if sources_change else words.preview_next_setting)
        return "\n".join(lines)
    suggestions = _n(result.get("suggestions"))
    if suggestions:
        lines.append(
            f"Beakr recorded {_count(suggestions, 'suggestion')} to move the cards of the "
            "dropped types to another type, for a person to apply or dismiss."
        )
    lines.append(RECORDED)
    return "\n".join(lines)


def created_card_type_lines(card_types: dict[str, Any] | None) -> list[str]:
    """What a created project's reply says of its card type choices."""
    card_types = card_types or {}
    lines: list[str] = []
    if card_types.get("copy_from_project_id"):
        lines.append("Its card types were copied from the project you named.")
    if card_types.get("inherit") in SOURCES:
        lines.append(f"It is offered {SOURCES[card_types['inherit']]}.")
    if card_types.get("agent_may_change") is False:
        lines.append("Beakr does not add or change its card types; only people do.")
    return lines
