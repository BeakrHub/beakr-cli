"""Who filed a proposal, and the staged proposal a write returns, reach the reader.

ROOT CAUSE (2026-10-19): the engine now returns ``proposal: {id, content_hash,
filed_by}`` on a staged write and ``filed_by`` on every proposal read, and the
hosted MCP server prints the write's ``content_hash`` (an accept of a card-row
proposal needs it as ``reviewed_hash``). The CLI dropped all of it: ``kb add-*``
printed the text only, ``kb proposals ls|show`` and the stdio ``show_proposal``
named no filer, and the stdio ``knowledge_base_write`` result had no
``proposal``, so its text lacked the hash the hosted server prints.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import mcp_server
from beakr_cli.main import internal_app as app

API = "https://api.test"
PROPOSALS = f"{API}/v1/knowledge/wiki/proposals"
PROPOSAL_ID = "11111111-2222-3333-4444-555555555555"
PROJECT = "22222222-2222-2222-2222-222222222222"
HASH = "c" * 64
FILED_BY = {
    "user_id": "u-1",
    "user_name": "Dana Reyes",
    "credential": "cred-1",
    "client": "beakr-cli",
    "agent": "claude-code/2.1",
    "run": None,
    "text": "filed by Dana Reyes via claude-code/2.1",
}
PROPOSAL = {
    "id": PROPOSAL_ID,
    "proposal_type": "edit_section",
    "status": "pending",
    "page_title": "Acme MSA",
    "summary": "Add fact: Fee: 10k",
    "payload": {"section_id": "sec_facts"},
    "content_hash": HASH,
    "filed_by": FILED_BY,
}
STAGED = {
    "content": [
        {
            "type": "text",
            "text": f'Proposed section edit to "Acme MSA".\nProposal id: {PROPOSAL_ID}',
        }
    ],
    "outcome": {"status": "success"},
    "proposal": {"id": PROPOSAL_ID, "content_hash": HASH, "filed_by": FILED_BY},
}
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


ADD_FACT = [
    "kb",
    "add-fact",
    "Acme MSA",
    "--attribute",
    "Fee",
    "--value",
    "10k",
    "--evidence",
    "{{doc:1}}",
    "--rationale",
    "order form",
]


@respx.mock
@pytest.mark.parametrize("as_person", [True, False], ids=["terminal", "piped"])
def test_kb_add_fact_prints_the_staged_proposals_content_hash(monkeypatch, as_person) -> None:
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: not as_person)
    respx.post(f"{API}/v1/knowledge/wiki/write").respond(json=STAGED)
    result = runner.invoke(app, ADD_FACT)
    assert result.exit_code == 0, result.output
    assert f"Proposal id: {PROPOSAL_ID}" in result.stdout
    assert f"content_hash: {HASH}" in result.stdout


@respx.mock
def test_kb_add_fact_json_passes_the_proposal_through() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/write").respond(json=STAGED)
    result = runner.invoke(app, [*ADD_FACT, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["proposal"] == STAGED["proposal"]


@respx.mock
def test_kb_proposals_ls_names_who_filed_each(terminal) -> None:
    respx.get(PROPOSALS).respond(json={"data": [PROPOSAL], "has_more": False})
    table = runner.invoke(app, ["kb", "proposals", "ls", "--project", PROJECT])
    assert table.exit_code == 0, table.output
    assert "Filed by" in table.stdout and "Dana Reyes" in table.stdout


@respx.mock
def test_kb_proposals_ls_piped_carries_filed_by(monkeypatch) -> None:
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: True)
    respx.get(PROPOSALS).respond(json={"data": [PROPOSAL], "has_more": False})
    piped = runner.invoke(app, ["kb", "proposals", "ls", "--project", PROJECT])
    assert piped.exit_code == 0, piped.output
    assert "filed_by=Dana Reyes via claude-code/2.1" in piped.stdout


@respx.mock
def test_kb_proposals_show_says_who_filed_it(terminal) -> None:
    respx.get(f"{PROPOSALS}/{PROPOSAL_ID}").respond(json=PROPOSAL)
    shown = runner.invoke(app, ["kb", "proposals", "show", PROPOSAL_ID])
    assert shown.exit_code == 0, shown.output
    assert "Filed by Dana Reyes via claude-code/2.1" in shown.stdout


@pytest.mark.asyncio
@respx.mock
async def test_the_stdio_show_proposal_says_who_filed_it() -> None:
    respx.get(f"{PROPOSALS}/{PROPOSAL_ID}").mock(return_value=httpx.Response(200, json=PROPOSAL))
    shown = await mcp_server.show_proposal(PROPOSAL_ID)
    assert "Filed by Dana Reyes via claude-code/2.1" in shown
    assert f"content_hash: {HASH}" in shown


def test_a_proposal_without_filed_by_prints_no_filer_line() -> None:
    # An engine older than filed_by sends none: no line, not "Filed by None".
    from beakr_cli.proposal_view import filed_by_line

    assert filed_by_line({"id": PROPOSAL_ID}) is None
