"""How a proposal was decided reaches the agent, and show_proposal reads as a summary.

ROOT CAUSES (agent tool review, 2026-11-06):

- Under auto-approve, Beakr applied a write before the agent's own
  accept_proposal reached it, and the engine answered that accept with 400
  "Proposal is accepted, not pending": the agent reported a failure for a
  proposal that was applied. The engine now answers a repeated accept with 200,
  ``already_accepted: true`` and who decided it (``reviewed_by_name``,
  ``reviewed_at``, ``auto_approved``); proposal reads carry the same fields and
  write replies an ``approval_state``. The stdio server printed none of it.
- show_proposal appended the whole payload as indented JSON: page text twice,
  and each conversation citation with the same text in ``meta.quote`` and
  ``meta.excerpt`` plus its own ``filed_by`` block. It now prints a summary and
  keeps the JSON behind ``raw=true``.

Every case also runs against an engine without the new fields.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli import mcp_server
from beakr_cli.mcp_server import mcp

API = "https://api.example.com"
PROPOSAL = "9d122ac5-777c-4791-96a6-2b69a079fb6d"
PROPOSALS = f"{API}/v1/knowledge/wiki/proposals"
HASH = "5" * 64
AT = "2026-11-06T17:21:02+00:00"
QUOTE = "Max: 'write knowledge where it makes most sense, or to several projects.'"
CITATION_FILER = {"run": "stdio:4079", "agent": "claude-code/2.1.319", "session": "e192-sess"}


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


def _proposal(**decided: Any) -> dict[str, Any]:
    """A proposal read shaped like the engine's, for an edit with one conversation citation."""
    citation = {
        "key": "conversation:claude-pages",
        "source_type": "conversation",
        "source_title": "Claude Code session: bootstrap pages",
        "stance": "support",
        "meta": {"quote": QUOTE, "excerpt": QUOTE, "filed_by": CITATION_FILER},
    }
    return {
        "id": PROPOSAL,
        "proposal_type": "edit",
        "status": "accepted" if decided else "pending",
        "page_title": "Claude Rules",
        "summary": "Write knowledge where it makes most sense.",
        "content_hash": HASH,
        "changes_card_tables": False,
        "filed_by": {"text": "filed by Maxwell Strome via claude-code/2.1.319"},
        "payload": {
            "type": "edit",
            "summary": "Write knowledge where it makes most sense.",
            "rationale": "Max asked for it.",
            "current_content": "## Knowledge writes\n\nOld rule. " * 20,
            "proposed_content": "## Knowledge writes\n\nNew rule. " * 20,
            "sections": [
                {
                    "id": "sec_knowledge_writes",
                    "title": "Knowledge writes",
                    "body": "New rule. " * 20,
                    "citations": [citation],
                }
            ],
        },
        **decided,
    }


async def _show(proposal: dict[str, Any], **arguments: Any) -> str:
    with respx.mock:
        respx.get(f"{PROPOSALS}/{PROPOSAL}").mock(return_value=Response(200, json=proposal))
        result = await _call("show_proposal", {"proposal_id": PROPOSAL, **arguments})
    assert not result.isError, result.content
    return result.content[0].text


@pytest.mark.asyncio
async def test_show_proposal_is_a_summary_with_the_filer_once() -> None:
    text = await _show(_proposal())
    lines = text.splitlines()
    assert lines[:4] == [
        f"Proposal {PROPOSAL}",
        "Filed by Maxwell Strome via claude-code/2.1.319",
        "Type: edit",
        "Status: pending",
    ]
    assert f"content_hash: {HASH}" in lines
    assert "Rationale: Max asked for it." in lines
    assert "Sections changed (1):" in lines
    assert "  - Knowledge writes (sec_knowledge_writes)" in lines
    citations = [line for line in lines if line.startswith("  - conversation:claude-pages")]
    assert citations == [
        "  - conversation:claude-pages · Claude Code session: bootstrap pages · support · "
        f'"{QUOTE}"'
    ]
    # No payload JSON, no second copy of the quote, no per-citation filer.
    assert "Payload:" not in text and "Old rule." not in text
    assert text.count(QUOTE) == 1
    assert "e192-sess" not in text and text.lower().count("filed by") == 1
    assert "Decided:" not in text
    assert text.endswith("show_proposal with raw=true.")


@pytest.mark.asyncio
async def test_show_proposal_raw_keeps_the_payload_json() -> None:
    proposal = _proposal()
    text = await _show(proposal, raw=True)
    assert text.split("\n\nPayload:\n", 1)[1] == json.dumps(proposal["payload"], indent=2)


@pytest.mark.asyncio
async def test_a_differing_excerpt_is_shown_beside_the_quote() -> None:
    proposal = _proposal()
    meta = proposal["payload"]["sections"][0]["citations"][0]["meta"]
    meta["excerpt"] = "The longer passage around it."
    text = await _show(proposal)
    assert 'excerpt: "The longer passage around it."' in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decided", "line"),
    [
        (
            {"reviewed_at": AT, "reviewed_by_name": None, "auto_approved": True},
            f"Decided: applied by auto-approve at {AT}",
        ),
        (
            {"reviewed_at": AT, "reviewed_by_name": "Dana Reyes", "auto_approved": False},
            f"Decided: accepted by Dana Reyes at {AT}",
        ),
    ],
)
async def test_show_proposal_says_how_a_decided_proposal_was_decided(decided, line) -> None:
    assert line in (await _show(_proposal(**decided))).splitlines()


@pytest.mark.asyncio
async def test_show_proposal_says_when_only_a_person_may_accept_it() -> None:
    # The hosted server printed this line and the stdio server did not, so an
    # agent on the stdio server called accept_proposal only to be refused.
    from beakr_cli.proposal_view import NEEDS_A_PERSON_LINE

    pending = {**_proposal(), "needs_person_to_accept": True}
    assert NEEDS_A_PERSON_LINE in (await _show(pending)).splitlines()
    assert "Needs a person" not in await _show(_proposal())
    assert "Needs a person" not in await _show({**pending, "status": "accepted"})


async def _accept(reply: dict[str, Any]) -> str:
    with respx.mock:
        respx.get(f"{PROPOSALS}/{PROPOSAL}").mock(return_value=Response(200, json=_proposal()))
        respx.post(f"{PROPOSALS}/{PROPOSAL}/accept").mock(return_value=Response(200, json=reply))
        result = await _call("accept_proposal", {"proposal_id": PROPOSAL, "reviewed_hash": HASH})
    assert not result.isError, result.content
    return result.content[0].text


@pytest.mark.asyncio
async def test_accepting_an_auto_approved_proposal_says_it_was_already_applied() -> None:
    text = await _accept(
        {
            "proposal_id": PROPOSAL,
            "already_accepted": True,
            "reviewed_at": AT,
            "reviewed_by_name": None,
            "auto_approved": True,
        }
    )
    assert text.startswith(f"Already applied (by auto-approve at {AT})")
    assert "Accepted proposal" not in text


@pytest.mark.asyncio
async def test_accepting_a_proposal_a_person_accepted_names_them() -> None:
    text = await _accept(
        {
            "proposal_id": PROPOSAL,
            "already_accepted": True,
            "reviewed_at": AT,
            "reviewed_by_name": "Dana Reyes",
            "auto_approved": False,
        }
    )
    assert text.startswith(f"Already accepted by Dana Reyes at {AT}")


@pytest.mark.asyncio
async def test_an_accept_from_an_engine_without_the_fields_reads_as_before() -> None:
    text = await _accept({"status": "accepted", "proposal_id": PROPOSAL, "pages_updated": 1})
    assert text == f"Accepted proposal {PROPOSAL} (1 page(s) updated)."


def _write_reply(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "outcome": {"status": "success"},
        "proposal": {"id": PROPOSAL, "content_hash": HASH},
        **extra,
    }


async def _write(reply: dict[str, Any]) -> str:
    with respx.mock:
        respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=Response(200, json=reply))
        result = await _call(
            "knowledge_base_write",
            {"action": "archive", "arguments": {"page": "Old", "rationale": "gone"}},
        )
    assert not result.isError, result.content
    return result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "meaning"),
    [
        ("pending", "accept_proposal applies it only when the user asks"),
        ("auto_apply_queued", "do not call accept_proposal"),
        ("needs_person", "a person must accept it in Beakr"),
    ],
)
async def test_a_writes_approval_state_is_printed(state: str, meaning: str) -> None:
    text = await _write(
        _write_reply(f"Proposed archive. Proposal id: {PROPOSAL}", approval_state=state)
    )
    line = [line for line in text.splitlines() if line.startswith("approval_state:")]
    assert line == [f"approval_state: {state} -- {_meaning(state)}"]
    assert meaning in line[0]


def _meaning(state: str) -> str:
    from beakr_cli.mcp_render import _APPROVAL_STATES

    return _APPROVAL_STATES[state]


@pytest.mark.asyncio
async def test_an_approval_state_the_text_already_gives_is_not_repeated() -> None:
    reply = _write_reply("Proposed archive.\napproval_state: pending", approval_state="pending")
    assert (await _write(reply)).count("approval_state") == 1


@pytest.mark.asyncio
async def test_an_approval_state_in_the_outcome_is_read_too() -> None:
    reply = _write_reply("Proposed archive.")
    reply["outcome"]["approval_state"] = "needs_person"
    assert "approval_state: needs_person" in await _write(reply)


@pytest.mark.asyncio
async def test_a_write_from_an_engine_without_approval_state_has_no_line() -> None:
    assert "approval_state" not in await _write(_write_reply("Proposed archive."))


def test_the_proposals_command_documents_every_status_it_takes() -> None:
    # O2: the docstring said ``status: pending|dismissed``; the engine also lists accepted.
    doc = mcp_server.knowledge_base.__doc__ or ""
    assert "pending (default), accepted or dismissed" in doc
