"""A session's read trail, and a write's (beakr-engine ``max/sh-read-trail``).

What each piece promises, one test or more each:

- ``beakr session trail`` reads ``GET /v1/sessions/{id}/trail`` with the person's
  credential, newest first, and prints each read's op, projects, summary and items;
  piped, the same lines; ``--limit`` and ``--cursor`` page it;
- ``beakr kb proposals show --trail`` adds the write's trail, grouped by project
  with the write's own first, other projects marked, sources outside Beakr last;
  without ``--trail`` nothing more is asked;
- a write with no recorded trail says so;
- the ``session_status`` tool shows both.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import respx
from typer.testing import CliRunner

from beakr_cli import mcp_session, scope_session, session_trail
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import McpSession, TokenState

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
PERSON_KEY = "bk_live_person_key_for_tests"
PROPOSAL = "prop-1"

runner = CliRunner()


def _setup(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)


def _read(op: str, *projects: str, summary: str | None = None, items=(), at: str = "") -> dict:
    names = {A: "Pharma A", B: "Pharma B"}
    return {
        "id": f"r-{op}",
        "session_id": SID,
        "at": at or "2026-11-11T03:10:00+00:00",
        "op": op,
        "projects": [{"id": p, "name": names[p]} for p in projects],
        "items": list(items),
        "summary": summary,
        "request_id": "req-1",
        "client": "beakr-mcp/0.4.0",
        "agent": "claude-code/2.1",
        "redacted": False,
    }


_PAGE = {
    "data": [
        _read(
            "cat",
            B,
            summary="Pharma B > Stability",
            items=[{"kind": "page", "id": "pg-2", "title": "Stability", "project_id": B}],
            at="2026-11-11T03:12:00+00:00",
        ),
        _read("search", A, summary='"assay results"'),
    ],
    "next_cursor": "c-2",
    "has_more": True,
}

_WRITE_TRAIL = {
    "write": {
        "kind": "proposal",
        "id": PROPOSAL,
        "project": {"id": A, "name": "Pharma A"},
        "at": "2026-11-11T03:13:00+00:00",
    },
    "session_id": SID,
    "recorded": True,
    "cross_project": True,
    "groups": [
        {
            "project": {"id": A, "name": "Pharma A"},
            "cross_project": False,
            "external": False,
            "reads": [_read("search", A, summary='"assay results"')],
        },
        {
            "project": {"id": B, "name": "Pharma B"},
            "cross_project": True,
            "external": False,
            "reads": [_read("cat", B, summary="Pharma B > Stability")],
        },
        {
            "project": None,
            "cross_project": False,
            "external": True,
            "reads": [
                _read(
                    "research",
                    items=[{"kind": "message", "title": "#lab", "service": "external:slack"}],
                )
            ],
        },
    ],
    "reads_total": 3,
    "truncated": False,
}


@respx.mock
def test_session_trail_prints_the_reads_newest_first(monkeypatch):
    _setup(monkeypatch)
    route = respx.get(f"{API}/v1/sessions/{SID}/trail").respond(json=_PAGE)

    result = runner.invoke(app, ["session", "trail", SID, "--limit", "2", "--cursor", "c-1"])

    assert result.exit_code == 0, result.output
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {PERSON_KEY}"
    assert request.url.params["limit"] == "2" and request.url.params["cursor"] == "c-1"
    lines = result.output.splitlines()
    assert lines[0] == f"Read trail of session {SID} (newest first):"
    assert "cat" in lines[1] and "Pharma B" in lines[1] and "Stability" in lines[1]
    assert "search" in lines[2] and "Pharma A" in lines[2] and '"assay results"' in lines[2]
    assert "by claude-code/2.1" in lines[1]
    assert "next_cursor: c-2" in result.output


@respx.mock
def test_session_trail_json_is_the_apis_page(monkeypatch):
    _setup(monkeypatch)
    respx.get(f"{API}/v1/sessions/{SID}/trail").respond(json=_PAGE)
    result = runner.invoke(app, ["session", "trail", SID, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"][0]["op"] == "cat"


def test_an_empty_trail_says_nothing_was_read():
    assert session_trail.render_trail({"data": [], "has_more": False}) == (
        "Read trail: nothing read yet."
    )


@respx.mock
def test_proposals_show_trail_groups_the_reads_by_project(monkeypatch):
    _setup(monkeypatch)
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").respond(
        json={"id": PROPOSAL, "proposal_type": "create", "status": "pending", "content_hash": "h"}
    )
    trail = respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/trail").respond(
        json=_WRITE_TRAIL
    )

    result = runner.invoke(app, ["kb", "proposals", "show", PROPOSAL, "--trail"])

    assert result.exit_code == 0, result.output
    assert trail.called
    text = result.output
    assert f"Read trail of proposal {PROPOSAL} into Pharma A (session {SID}): 3 reads" in text
    assert "Cross-project: its session read other projects before writing." in text
    own, other, outside = (
        text.index("Pharma A (this write's project):"),
        text.index("Pharma B (another project):"),
        text.index("Outside Beakr:"),
    )
    assert own < other < outside
    assert "#lab [external:slack]" in text


@respx.mock
def test_proposals_show_asks_for_no_trail_unless_told(monkeypatch):
    _setup(monkeypatch)
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").respond(
        json={"id": PROPOSAL, "proposal_type": "create", "status": "pending"}
    )
    trail = respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/trail").respond(
        json=_WRITE_TRAIL
    )
    result = runner.invoke(app, ["kb", "proposals", "show", PROPOSAL])
    assert result.exit_code == 0, result.output
    assert not trail.called and "Read trail" not in result.output


def test_a_write_without_a_recorded_trail_says_so():
    text = session_trail.render_write_trail(
        {
            "write": {"kind": "proposal", "id": PROPOSAL, "project": None, "at": ""},
            "session_id": None,
            "recorded": False,
            "cross_project": False,
            "groups": [],
            "reads_total": 0,
            "truncated": False,
        }
    )
    assert "none recorded" in text


@respx.mock
def test_session_status_shows_the_trail_and_sends_a_writes_trail_to_a_person(monkeypatch):
    """rc.9 QA: the write's trail answered 403 insufficient_scope to the session token.

    Refused on purpose (it can list other projects' reads), so the tool never asks
    for it, with the session's token or the person's own key: it says where a person
    reads it.
    """
    _setup(monkeypatch)
    held = McpSession(folder=None)
    held.state = TokenState(
        SID, "bk_st_first", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    )
    held.session = {"id": SID, "status": "active", "mode": "auto_lock", "lock_state": "open"}
    monkeypatch.setattr(scope_session, "ACTIVE", held)
    respx.get(f"{API}/v1/sessions/current").respond(json=held.session)
    page = respx.get(f"{API}/v1/sessions/{SID}/trail").respond(json=_PAGE)
    write = respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/trail").respond(
        json=_WRITE_TRAIL
    )

    text = asyncio.run(mcp_session.session_status(trail=True, proposal_id=PROPOSAL))

    assert isinstance(text, str), text
    assert page.calls.last.request.headers["authorization"] == "Bearer bk_st_first"
    assert "Read trail of session" not in text  # the tool's own session needs no id
    assert "Read trail (newest first):" in text
    assert not write.called
    assert "Pharma B (another project)" not in text
    assert f"beakr kb proposals show {PROPOSAL} --trail" in text
    assert "read by a person" in text


@respx.mock
def test_an_ended_sessions_status_never_reads_a_writes_trail_with_the_persons_key(monkeypatch):
    _setup(monkeypatch)
    held = McpSession(folder=None, agent="conv-1")
    held.ended_conversation = "conv-1"
    held.ended_session_id = SID
    monkeypatch.setattr(scope_session, "ACTIVE", held)
    respx.get(f"{API}/v1/sessions/{SID}").respond(
        json={"id": SID, "status": "ended", "mode": "auto_lock", "agent_session": "conv-1"}
    )
    write = respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/trail").respond(
        json=_WRITE_TRAIL
    )

    text = asyncio.run(mcp_session.session_status(proposal_id=PROPOSAL))

    assert isinstance(text, str) and "(ended)" in text
    assert not write.called
    assert f"beakr kb proposals show {PROPOSAL} --trail" in text


@respx.mock
def test_proposals_show_trail_in_a_session_says_a_person_reads_it(monkeypatch):
    """In a shell or agent conversation the CLI sends a session token: refused, said so."""
    _setup(monkeypatch)
    scope_session.save_state(
        TokenState(
            SID, "bk_st_shell", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        )
    )
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").respond(
        json={"id": PROPOSAL, "proposal_type": "create", "status": "pending"}
    )
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/trail").respond(
        403,
        json={
            "error": {
                "code": "insufficient_scope",
                "message": "This credential is not allowed to call this endpoint.",
            }
        },
    )

    result = runner.invoke(app, ["kb", "proposals", "show", PROPOSAL, "--trail"])

    assert result.exit_code != 0
    text = " ".join(result.output.split())
    assert "read by a person, not by an agent's session" in text
    assert "Traceback" not in result.output
