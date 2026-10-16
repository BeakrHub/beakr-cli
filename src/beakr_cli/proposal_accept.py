"""Accept a Knowledge proposal bound to the read that reviewed it.

The engine returns a ``content_hash`` with every proposal read and refuses an
accept whose ``reviewed_hash`` no longer matches the stored proposal (409
``proposal_changed``); for a proposal that changes a map card's table rows it
can require the hash. So every accept here reads the proposal first (or uses
the read the command just showed), and echoes that read's ``content_hash``. The
hash is never computed on this side: it is the server's statement of what was
read.
"""

from __future__ import annotations

from typing import Any

PROPOSAL_CHANGED = "proposal_changed"
CHANGED_MESSAGE = "This proposal changed after it was read, so nothing was applied."
CHANGED_FIX = (
    "Read it again (show_proposal, or beakr kb proposals show), review what it now "
    "proposes, and accept only if it is still wanted."
)


def _body(proposal: dict) -> dict | None:
    content_hash = proposal.get("content_hash")
    return {"reviewed_hash": content_hash} if content_hash else None


def accept_reviewed(sdk: Any, proposal_id: str, proposal: dict | None = None) -> tuple[dict, dict]:
    """``(the proposal as read, the accept's result)``; reads it first unless given."""
    read = proposal if proposal and proposal.get("content_hash") else None
    if read is None:
        read = sdk.knowledge.proposals.show(proposal_id) or {}
    result = sdk.api.knowledge.accept_proposal(proposal_id, body=_body(read))
    return read, result or {}


async def accept_reviewed_async(sdk: Any, proposal_id: str) -> tuple[dict, dict]:
    """:func:`accept_reviewed` on the async SDK (the stdio MCP server)."""
    read = await sdk.knowledge.proposals.show(proposal_id) or {}
    result = await sdk.api.knowledge.accept_proposal(proposal_id, body=_body(read))
    return read, result or {}


def accepted_text(proposal: dict, result: dict, proposal_id: str) -> str:
    """What was accepted, as the hosted MCP server words it (``formatting.accepted_proposal``)."""
    line = f"Accepted proposal {result.get('proposal_id', proposal_id)}"
    if proposal.get("proposal_type"):
        what = str(proposal["proposal_type"])
        if proposal.get("page_title"):
            what += f" on {proposal['page_title']}"
        line += f" ({what})"
    if proposal.get("summary"):
        line += f": {proposal['summary']}"
    return line + "."
