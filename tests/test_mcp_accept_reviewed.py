"""The stdio MCP server's accept_proposal applies only the proposal the caller reviewed.

ROOT CAUSE THIS PINS (security review, 2026-10-16): accept_proposal read the
proposal and echoed that fresh read's ``content_hash`` as ``reviewed_hash``. That
bound the accept to whatever the proposal was at accept time, not to the
``show_proposal`` the user reviewed, so a proposal that changed in between was
accepted anyway: the fingerprint was a rubber stamp. The caller now passes the
``content_hash`` show_proposal printed; nothing here takes a hash from a read of
its own, and a card-table proposal without one is refused.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from beakr_cli import mcp_server
from beakr_cli.mcp_errors import ApiCallError

API = "https://api.thebeakr.com"
PROPOSAL = "11111111-2222-3333-4444-555555555555"
PROPOSALS = f"{API}/v1/knowledge/wiki/proposals"
HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_test_x")
    monkeypatch.setenv("BEAKR_API_URL", API)


class _Proposal:
    """The proposal routes as far as accept binding goes: a read returns the stored
    proposal's current hash, and an accept refuses a reviewed_hash that differs."""

    def __init__(self, *, content_hash: str, changes_card_tables: bool) -> None:
        self.content_hash = content_hash
        self.changes_card_tables = changes_card_tables
        self.read = respx.get(f"{PROPOSALS}/{PROPOSAL}").mock(side_effect=self._read)
        self.accept = respx.post(f"{PROPOSALS}/{PROPOSAL}/accept").mock(side_effect=self._accept)

    def _read(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": PROPOSAL,
                "proposal_type": "edit",
                "page_title": "Acme MSA",
                "status": "pending",
                "content_hash": self.content_hash,
                "changes_card_tables": self.changes_card_tables,
            },
        )

    def _accept(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        reviewed = (body or {}).get("reviewed_hash")
        if reviewed is not None and reviewed != self.content_hash:
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "proposal_changed",
                        "message": "This proposal changed after it was reviewed.",
                    }
                },
            )
        return httpx.Response(
            200, json={"status": "accepted", "proposal_id": PROPOSAL, "pages_updated": 1}
        )

    def accept_bodies(self) -> list[dict | None]:
        return [
            json.loads(call.request.content) if call.request.content else None
            for call in self.accept.calls
        ]


@pytest.mark.asyncio
@respx.mock
async def test_a_proposal_changed_after_it_was_shown_is_refused_with_the_shown_hash():
    proposal = _Proposal(content_hash=HASH_A, changes_card_tables=True)
    shown = await mcp_server.show_proposal(PROPOSAL)
    assert f"content_hash: {HASH_A}" in shown
    proposal.content_hash = HASH_B  # the stored proposal changes after the review

    with pytest.raises(ApiCallError) as raised:
        await mcp_server.accept_proposal(PROPOSAL, reviewed_hash=HASH_A)

    assert raised.value.fields["error_code"] == "proposal_changed"
    assert "nothing was applied" in raised.value.fields["message"]
    assert "show_proposal" in raised.value.fields["fix"]
    assert proposal.accept_bodies() == [{"reviewed_hash": HASH_A}]


@pytest.mark.asyncio
@respx.mock
async def test_a_card_table_proposal_without_a_reviewed_hash_is_refused_before_any_accept():
    proposal = _Proposal(content_hash=HASH_B, changes_card_tables=True)

    result = await mcp_server.accept_proposal(PROPOSAL)

    assert result["status"] == "error"
    assert result["error_code"] == "review_required"
    assert "reviewed_hash" in result["fix"]
    assert not proposal.accept.called


@pytest.mark.asyncio
@respx.mock
async def test_a_prose_proposal_without_a_hash_is_accepted_with_none_not_a_fresh_reads_hash():
    proposal = _Proposal(content_hash=HASH_B, changes_card_tables=False)

    text = await mcp_server.accept_proposal(PROPOSAL)

    assert proposal.accept_bodies() == [None]
    assert text == f"Accepted proposal {PROPOSAL} (1 page(s) updated)."


@pytest.mark.asyncio
@respx.mock
async def test_the_reviewed_hash_is_sent_as_given_with_no_read_of_its_own():
    proposal = _Proposal(content_hash=HASH_A, changes_card_tables=True)

    await mcp_server.accept_proposal(PROPOSAL, reviewed_hash=HASH_A)

    assert not proposal.read.called
    assert proposal.accept_bodies() == [{"reviewed_hash": HASH_A}]


@pytest.mark.asyncio
@respx.mock
async def test_a_reviewed_hash_that_is_not_a_content_hash_is_refused():
    proposal = _Proposal(content_hash=HASH_A, changes_card_tables=True)

    result = await mcp_server.accept_proposal(PROPOSAL, reviewed_hash="latest")

    assert result["error_code"] == "invalid_arguments"
    assert not proposal.read.called and not proposal.accept.called
