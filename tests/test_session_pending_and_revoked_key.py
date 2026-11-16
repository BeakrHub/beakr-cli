"""`beakr session start --pending` in an automatic organization, and `beakr mcp` with a
revoked key (1.0.2 QA, m4 and m16).

- m4: the API has no pending flag: a session started without lists takes the
  organization's mode, so in an automatic organization `--pending` (or a start with
  no lists away from a terminal) started an open session that reads every project.
  It is refused there now, and still starts pending where every session must pick.
- m16: a 401 while starting `beakr mcp`'s session was reported as session_unavailable
  with "Retry shortly", so agents retried in a loop. A revoked or invalid key is not
  transient: the refusal says to make a new key, and never to retry.
"""

from __future__ import annotations

import asyncio

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import mcp_session, scope_session
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import McpSession, SessionScopeError

API = "https://api.test"
ORG = "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person")
    for name in ("BEAKR_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)


def _org(mode: str) -> None:
    respx.get(f"{API}/v1/me/organization").respond(json={"id": ORG})
    respx.get(f"{API}/v1/me").respond(json={"active_org": {"id": ORG}, "organization": {"id": ORG}})
    respx.get(f"{API}/v1/organizations/{ORG}/project-isolation").respond(
        json={"session_mode": mode, "available": {"server_agent_sessions": True}}
    )


def _text(result) -> str:
    return " ".join(result.output.replace("│", " ").split())


@respx.mock
def test_pending_is_refused_where_sessions_start_automatic():
    _org("automatic")
    started = respx.post(f"{API}/v1/sessions")

    result = runner.invoke(app, ["session", "start", "--pending"])

    assert result.exit_code != 0, result.output
    text = _text(result)
    assert "Your organization starts sessions automatic" in text
    assert "can't start pending here" in text
    assert "--automatic" in text
    assert not started.called


@respx.mock
def test_pending_still_starts_pending_where_every_session_must_pick():
    _org("must_pick")
    started = respx.post(f"{API}/v1/sessions").respond(
        201,
        json={
            "id": SID,
            "status": "pending",
            "mode": "picked",
            "read_projects": [],
            "write_projects": [],
            "picker_url": f"https://app.test/sessions/{SID}",
            "token": {"token": "bk_st_x", "expires_at": "2099-01-01T00:00:00+00:00"},
        },
    )

    result = runner.invoke(app, ["session", "start", "--pending", "--env"])

    assert result.exit_code == 0, result.output
    assert started.called
    assert f"export {scope_session.SESSION_ENV}={SID}" in result.stdout


@respx.mock
def test_a_revoked_key_is_not_retried_by_the_mcp_server():
    respx.post(f"{API}/v1/sessions").respond(
        401,
        json={"error": {"code": "unauthorized", "message": "Invalid or expired API key"}},
    )
    held = McpSession(folder=None, agent="conv-1")

    with pytest.raises(SessionScopeError) as refused:
        asyncio.run(held.ensure_started())

    assert refused.value.code == "unauthorized"
    assert "revoked or is invalid" in refused.value.message
    assert "Settings › Developer" in (refused.value.fix or "")
    assert "Retry shortly" not in f"{refused.value.message} {refused.value.fix}"


@respx.mock
def test_the_gate_answers_a_revoked_key_without_retry(monkeypatch):
    respx.post(f"{API}/v1/sessions").respond(
        401,
        json={"error": {"code": "unauthorized", "message": "Invalid or expired API key"}},
    )
    monkeypatch.setattr(scope_session, "ACTIVE", McpSession(folder=None, agent="conv-1"))

    class _Server:
        def get_context(self):
            raise ValueError("no request context in this test")

    refusal = asyncio.run(mcp_session.gate(_Server(), "knowledge_base", None))

    assert refusal["error_code"] == "unauthorized"
    assert "Retry shortly" not in f"{refusal['message']} {refusal['fix']}"
    assert "Settings › Developer" in refusal["fix"]
