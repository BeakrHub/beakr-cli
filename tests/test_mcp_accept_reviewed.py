"""The stdio MCP server's accept_proposal applies exactly the proposal it read.

ROOT CAUSE THIS PINS (2026-10-16): accept_proposal posted the accept with no
reviewed hash, so the engine could not bind it to a review, and the hash it can
require for a card-table proposal would have refused every accept. The tool now
reads the proposal and echoes that read's ``content_hash`` (never computing
one), says what it applied in the hosted server's words, and a proposal that
changed after the read is refused with "review it again".
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
READ = {
    "id": PROPOSAL,
    "proposal_type": "edit",
    "page_title": "Acme MSA",
    "summary": "Add fact: Fee: 10k",
    "status": "pending",
    "content_hash": "ab" * 32,
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_test_x")
    monkeypatch.setenv("BEAKR_API_URL", API)


@pytest.mark.asyncio
@respx.mock
async def test_accept_reads_the_proposal_and_echoes_its_content_hash():
    read = respx.get(f"{PROPOSALS}/{PROPOSAL}").respond(json=READ)
    accept = respx.post(f"{PROPOSALS}/{PROPOSAL}/accept").respond(
        json={"status": "accepted", "proposal_id": PROPOSAL}
    )

    text = await mcp_server.accept_proposal(PROPOSAL)

    assert read.called
    assert json.loads(accept.calls.last.request.content) == {"reviewed_hash": READ["content_hash"]}
    assert text == f"Accepted proposal {PROPOSAL} (edit on Acme MSA): Add fact: Fee: 10k."


@pytest.mark.asyncio
@respx.mock
async def test_a_proposal_changed_since_the_read_is_refused_with_review_again():
    respx.get(f"{PROPOSALS}/{PROPOSAL}").respond(json=READ)
    respx.post(f"{PROPOSALS}/{PROPOSAL}/accept").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": {
                    "code": "proposal_changed",
                    "message": "This proposal changed after it was reviewed.",
                }
            },
        )
    )

    with pytest.raises(ApiCallError) as raised:
        await mcp_server.accept_proposal(PROPOSAL)

    assert raised.value.fields["error_code"] == "proposal_changed"
    assert "nothing was applied" in raised.value.fields["message"]
    assert "show_proposal" in raised.value.fields["fix"]
