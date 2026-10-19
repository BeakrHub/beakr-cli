"""Accept a Knowledge proposal bound to the read someone reviewed.

The engine returns a ``content_hash`` with every proposal read and refuses an
accept whose ``reviewed_hash`` no longer matches the stored proposal (409
``proposal_changed``); for a proposal that changes a map card's table rows it
can require the hash, and the read says which those are (``changes_card_tables``).

The hash sent is always the one from the read that was reviewed: the
``show_proposal`` an agent read, the ``beakr kb proposals show`` a person read,
the proposal this command just displayed for confirmation, or the proposal it
just created. It is never taken from a read made at accept time, because that
binds the accept to whatever the proposal is then rather than to what was
reviewed, and a changed proposal would be accepted anyway. Nor is it computed on
this side: it is the server's statement of what was read.

Without a reviewed hash, the proposal is read only to learn whether it changes
card tables. Such an accept is refused here whether or not the server requires
the hash yet; any other is sent with no hash, as the server allows.
"""

from __future__ import annotations

import re
from typing import Any

PROPOSAL_CHANGED = "proposal_changed"
CHANGED_MESSAGE = "This proposal changed after it was reviewed, so nothing was applied."
CHANGED_FIX = (
    "Read it again (show_proposal, or beakr kb proposals show), review what it now "
    "proposes, and accept it with the new content_hash only if it is still wanted."
)
REVIEW_REQUIRED = "review_required"
REVIEW_REQUIRED_MESSAGE = (
    "This proposal changes a card's table rows, so it is accepted only with the "
    "content_hash of the read that was reviewed. Nothing was applied."
)
INVALID_HASH_MESSAGE = (
    "reviewed_hash must be the content_hash the proposal read printed (64 hex characters)."
)

_HASH = re.compile(r"^[0-9a-fA-F]{64}$")


class ReviewRequired(Exception):
    """A card-table proposal was to be accepted with no reviewed hash; nothing was sent."""

    def __init__(self, proposal: dict) -> None:
        super().__init__(REVIEW_REQUIRED_MESSAGE)
        self.proposal = proposal


def checked_hash(reviewed_hash: str | None) -> str | None:
    """The reviewed hash as given, or None; ValueError for anything not a content hash."""
    given = (reviewed_hash or "").strip()
    if not given:
        return None
    if not _HASH.match(given):
        raise ValueError(INVALID_HASH_MESSAGE)
    return given


def _body(reviewed_hash: str | None) -> dict | None:
    return {"reviewed_hash": reviewed_hash} if reviewed_hash else None


def accept_reviewed(sdk: Any, proposal_id: str, reviewed_hash: str | None) -> dict:
    """Accept ``proposal_id`` bound to ``reviewed_hash``; refuse a hashless card-table accept."""
    reviewed_hash = checked_hash(reviewed_hash)
    if reviewed_hash is None:
        proposal = sdk.knowledge.proposals.show(proposal_id) or {}
        if proposal.get("changes_card_tables"):
            raise ReviewRequired(proposal)
    return sdk.api.knowledge.accept_proposal(proposal_id, body=_body(reviewed_hash)) or {}


async def accept_reviewed_async(sdk: Any, proposal_id: str, reviewed_hash: str | None) -> dict:
    """:func:`accept_reviewed` on the async SDK (the stdio MCP server)."""
    reviewed_hash = checked_hash(reviewed_hash)
    if reviewed_hash is None:
        proposal = await sdk.knowledge.proposals.show(proposal_id) or {}
        if proposal.get("changes_card_tables"):
            raise ReviewRequired(proposal)
    return await sdk.api.knowledge.accept_proposal(proposal_id, body=_body(reviewed_hash)) or {}


def review_lines(proposal: dict) -> list[str]:
    """The lines a proposal read adds so it can be accepted bound to that read."""
    lines = []
    if proposal.get("content_hash"):
        lines.append(f"content_hash: {proposal['content_hash']}")
    if proposal.get("changes_card_tables"):
        lines.append("Changes card table rows: accept only with reviewed_hash.")
    return lines


def accepted_text(result: dict, proposal_id: str) -> str:
    """What was accepted, as the hosted MCP server words it (``formatting.accepted_proposal``)."""
    line = f"Accepted proposal {result.get('proposal_id', proposal_id)}"
    counts = [
        f"{result[key]} page(s) {label}"
        for key, label in (("pages_created", "created"), ("pages_updated", "updated"))
        if result.get(key)
    ]
    if counts:
        line += f" ({', '.join(counts)})"
    return line + "."
