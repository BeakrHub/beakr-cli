"""``403 session_project_required`` (engine #2651): a call in a session that must name a
project named none. It used to answer ``insufficient_scope``, whose fix pointed at a
missing key scope; the CLI and ``beakr mcp`` now say how to name one of the session's
projects.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import scope_session
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import McpSession, TokenState

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"

runner = CliRunner()

_REFUSAL = {
    "detail": {
        "code": "session_project_required",
        "message": "Research in a session must name a project (`project_id`).",
        "fix": "Pass `project_id` with one of the session's projects (GET /v1/sessions/current "
        "lists them), and repeat the call for each project you need.",
        "session_id": SID,
    },
    "error": {
        "code": "session_project_required",
        "message": "Research in a session must name a project (`project_id`).",
        "fix": "Pass `project_id` with one of the session's projects.",
    },
}


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person")
    scope_session.ACTIVE = None
    yield
    scope_session.ACTIVE = None


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


@respx.mock
def test_the_cli_says_to_name_one_of_the_sessions_projects(monkeypatch):
    scope_session.save_state(TokenState(SID, "bk_st_shell", _later()))
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    respx.post(f"{API}/v1/knowledge/research").respond(403, json=_REFUSAL)

    result = runner.invoke(app, ["research", "What changed?"])

    assert result.exit_code == 3, result.output
    text = " ".join(result.output.split())
    assert "Research in a session must name a project" in text
    assert "with --project (`beakr session show` lists them)" in text
    assert "lacks the" not in text and "beakr keys create" not in text


@respx.mock
def test_beakr_mcp_says_to_pass_scope():
    from beakr_cli.mcp_server import mcp

    held = McpSession(folder=None)
    held.state = TokenState(SID, "bk_st_first", _later())
    held.session = {"id": SID, "status": "active", "read_projects": [], "write_projects": []}
    scope_session.ACTIVE = held
    respx.post(f"{API}/v1/knowledge/research").respond(403, json=_REFUSAL)

    result = asyncio.run(mcp.call_tool("research", {"question": "What changed?"}))

    assert result.isError
    body = json.loads(result.content[0].text)
    assert body["error_code"] == "session_project_required"
    assert "Pass `scope`" in body["fix"] and "session_status lists them" in body["fix"]
