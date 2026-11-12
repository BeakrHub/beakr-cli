"""Accepting a proposal is the person's own review, outside any session (engine #2643).

The API treats any request inside a session (a ``bk_st_`` token, or a key joined
with ``X-Beakr-Session``) as an agent's: it may not accept where a person must, nor
accept a held proposal. From a plain terminal ``beakr kb proposals accept`` (and
``kb propose ... --accept``) is the person's review, so it is sent with their own key
and no session, even in a shell session or a terminal that kept one, and no session
is started for it. Inside an agent conversation it stays the agent's, in its session.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import scope_session, write_session
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import TokenState

API = "https://api.test"
PERSON = "bk_live_reviewer"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
PROPOSAL = "prop-1"
HASH = "a" * 64

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)
    monkeypatch.setattr(write_session, "ENABLED", True)
    monkeypatch.setattr(write_session, "_interactive", lambda: False)


def _accepted():
    return respx.post(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/accept").respond(
        json={"status": "accepted", "proposal_id": PROPOSAL}
    )


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


@respx.mock
def test_a_plain_terminal_accepts_as_the_person_and_starts_no_session():
    started = respx.post(f"{API}/v1/sessions").respond(201, json={})
    accepted = _accepted()

    result = runner.invoke(app, ["kb", "proposals", "accept", PROPOSAL, "--reviewed-hash", HASH])

    assert result.exit_code == 0, result.output
    request = accepted.calls.last.request
    assert request.headers["authorization"] == f"Bearer {PERSON}"
    assert "x-beakr-session" not in request.headers
    assert not started.called


@respx.mock
def test_a_shell_session_still_accepts_as_the_person(monkeypatch):
    scope_session.save_state(TokenState(SID, "bk_st_shell", _later()))
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    respx.get(f"{API}/v1/sessions/{SID}").respond(
        json={"id": SID, "status": "active", "read_projects": [], "write_projects": []}
    )
    accepted = _accepted()

    result = runner.invoke(app, ["kb", "proposals", "accept", PROPOSAL, "--reviewed-hash", HASH])

    assert result.exit_code == 0, result.output
    request = accepted.calls.last.request
    assert request.headers["authorization"] == f"Bearer {PERSON}"
    assert "x-beakr-session" not in request.headers


@respx.mock
def test_inside_an_agent_conversation_the_accept_stays_the_agents(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-123")
    accepted = _accepted()

    result = runner.invoke(app, ["kb", "proposals", "accept", PROPOSAL, "--reviewed-hash", HASH])

    assert result.exit_code == 0, result.output
    request = accepted.calls.last.request
    # Joined to its conversation's session: the API decides, and refuses where a
    # person must accept (person_review_required) or the proposal is held.
    assert request.headers.get("x-beakr-session") == "conv-123"
