"""Resolving a review flag: which route applies a choice, and what it is sent.

A review flag (a proposal of type ``review``) is resolved in three ways, each
pinned to the page revision the person chose against:

- a drafted option (``POST .../resolutions`` drafts them): a card-table option
  (it carries a ``row_operation``) goes to ``POST .../resolve-rows``, any other
  to ``POST .../resolve`` as the section's new text;
- the person's own text for the flagged section: ``POST .../resolve``;
- for a contradiction between cards' rows, one outcome on every card it lists:
  ``POST .../resolve-cards``.

The page's revision is read before anything is drafted or applied: a page that
changed since the person chose is refused here, so a fresh draft of the new
revision is never applied unseen. ``beakr kb review`` and the stdio MCP server's
``resolve_review`` both plan their calls with these functions; the engine's
hosted ``resolve_review`` makes the same calls (``beakr/api/mcp_remote/tools.py``).
"""

from __future__ import annotations

import re
from typing import Any

from beakr_cli.review_flags import find_option, is_row_option

#: The outcomes ``resolve-cards`` takes.
OUTCOMES = ("keep_disputed", "adopt_disputing", "both_valid")

_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


class ResolveRefused(Exception):
    """A resolve refused before anything was sent: ``code``, ``message`` and ``fix``."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix

    def fields(self) -> dict[str, str]:
        return {
            "status": "error",
            "error_code": self.code,
            "message": self.message,
            "fix": self.fix,
        }


def base_path(proposal_id: str) -> str:
    if not _UUID.match(proposal_id or ""):
        raise ResolveRefused(
            "invalid_arguments",
            "proposal_id must be the proposal's UUID.",
            "Copy the review flag's id as a proposals listing prints it.",
        )
    return f"/v1/knowledge/wiki/proposals/{proposal_id}"


def is_decided(flag: dict[str, Any]) -> bool:
    """Whether the proposal read is a review flag that is no longer open.

    One already resolved (by a person, or by Beakr from a person's own interview
    answer) or dismissed has no options left to apply, so ``review_options`` says
    how it was decided instead, as the hosted tool does.
    """
    return flag.get("proposal_type") == "review" and flag.get("status") not in (None, "pending")


def page_changed(proposal_id: str) -> ResolveRefused:
    return ResolveRefused(
        "conflict",
        "The page changed since these options were drafted, so nothing was applied.",
        f"Call review_options(proposal_id='{proposal_id}') again and let the user choose "
        "from the new options.",
    )


def checked_choice(
    proposal_id: str,
    *,
    option_id: str | None,
    section_body: str | None,
    outcome: str | None,
    expected_revision: int | None,
) -> str:
    """Which way the flag is resolved: ``option``, ``section`` or ``outcome``."""
    chosen = [
        name
        for name, value in (
            ("option", option_id),
            ("section", section_body),
            ("outcome", outcome),
        )
        if value
    ]
    if len(chosen) != 1:
        raise ResolveRefused(
            "invalid_arguments",
            "Pass exactly one of option_id, section_body or outcome.",
            "Call review_options first and pass the option the user chose as option_id.",
        )
    if chosen[0] == "outcome":
        if outcome not in OUTCOMES:
            raise ResolveRefused(
                "invalid_arguments",
                f"outcome must be one of {', '.join(OUTCOMES)}.",
                "Pass the outcome the user chose.",
            )
    elif not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
        raise ResolveRefused(
            "invalid_arguments",
            "expected_revision is required: the page revision review_options printed.",
            f"Call review_options(proposal_id='{proposal_id}') and pass its page revision.",
        )
    return chosen[0]


def require_revision(proposal_id: str, answer: dict[str, Any], expected: int | None) -> None:
    """Refuse when the flag read or the drafts are of another page revision."""
    if answer.get("current_revision") != expected:
        raise page_changed(proposal_id)


def section_request(
    conflict: dict[str, Any], section_body: str, expected: int | None
) -> tuple[str, dict[str, Any]]:
    """The person's own text for the flagged section."""
    return "/resolve", {
        "section_id": conflict.get("section_id"),
        "new_section_body": section_body,
        "expected_revision": expected,
    }


def option_request(
    proposal_id: str,
    conflict: dict[str, Any],
    drafted: dict[str, Any],
    option_id: str,
    expected: int | None,
) -> tuple[str, dict[str, Any]]:
    """A drafted option, sent to the route that applies its kind."""
    option = find_option(drafted.get("options") or [], option_id)
    if option is None:
        raise ResolveRefused(
            "not_found",
            f"Option {option_id} is not among this flag's drafted options.",
            f"Call review_options(proposal_id='{proposal_id}') to see them.",
        )
    if is_row_option(option):
        return "/resolve-rows", {"option_id": option.get("id"), "expected_revision": expected}
    return "/resolve", {
        "section_id": drafted.get("section_id") or conflict.get("section_id"),
        "new_section_body": option.get("new_section_body"),
        "expected_revision": expected,
        "option_id": option.get("id"),
    }


#: What an agent is told when a person must resolve it (the accept's refusal).
PERSON_ONLY_MESSAGE = (
    "This project requires that a person, or a key with the proposals:accept (reviewer) "
    "permission, apply this resolution. This key or token is neither, so nothing was applied."
)
PERSON_ONLY_FIX = (
    "Ask the user to resolve it in Beakr, or to run it with a key that holds "
    "proposals:accept; do not retry with this key."
)
#: The same, for a person at the command line.
PERSON_ONLY_CLI_FIX = (
    "Resolve it signed in as yourself (`beakr auth login`, outside an agent session) or in "
    "the Beakr web app, or run it with an API key that holds the proposals:accept "
    "(reviewer) scope (BEAKR_API_KEY), as a scheduled job does."
)
