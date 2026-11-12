"""rc.9 QA findings for the CLI, fixed for rc.10.

- ``beakr projects profile setup`` outside a session starts none (it left two empty
  open sessions behind: one per one-turn run); inside one it still checks it;
- a bare ``beakr session show`` after ``beakr session end`` in a terminal that kept
  the session says it ended, and when, and exits 0 (it exited 2 with a usage box).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import scope_session, write_session
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import TokenState

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
P = "4dd25154-0d80-44ab-b4c2-167319bfacb4"

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person")
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)
    monkeypatch.setattr(write_session, "ENABLED", True)
    monkeypatch.setattr(write_session, "_interactive", lambda: False)


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def _session(status: str = "active", **extra) -> dict:
    return {
        "id": SID,
        "status": status,
        "mode": "auto_lock",
        "lock_state": "open" if status != "ended" else None,
        "agent_session": None,
        "read_projects": [],
        "write_projects": [],
        "touched_projects": [],
        "picker_url": f"https://app.test/sessions/{SID}",
        **extra,
    }


def _profile_api() -> dict[str, respx.Route]:
    events = 'event: stream_complete\ndata: {"status": "completed"}\n\n'
    return {
        "project": respx.get(f"{API}/v1/projects/{P}").respond(json={"id": P, "name": "Helios"}),
        "run": respx.post(f"{API}/v1/runs").respond(json={"run_id": "r1", "thread_id": "t1"}),
        "events": respx.get(f"{API}/v1/runs/r1/events").respond(
            text=events, headers={"content-type": "text/event-stream"}
        ),
        "status": respx.get(f"{API}/v1/runs/r1").respond(json={"status": "completed"}),
        "profile": respx.get(f"{API}/v1/projects/{P}/knowledge-profile").respond(json={}),
    }


@respx.mock
def test_profile_setup_outside_a_session_starts_none():
    sessions = respx.post(f"{API}/v1/sessions").respond(201, json=_session())
    routes = _profile_api()

    for message in ("hi", "Compounds and assays"):
        result = runner.invoke(
            app, ["projects", "profile", "setup", "--project", P, "--message", message]
        )
        assert result.exit_code == 0, result.output

    assert not sessions.called
    assert routes["run"].call_count == 2
    # The profile agent's run is the person's own, as before.
    assert routes["run"].calls.last.request.headers["authorization"] == "Bearer bk_live_person"


@respx.mock
def test_profile_setup_inside_a_kept_session_is_still_checked():
    scope_session.save_state(TokenState(SID, "bk_st_kept", _later()))
    scope_session.remember_terminal(SID, mode="auto_lock")
    respx.get(f"{API}/v1/sessions/{SID}").respond(
        json=_session(
            lock_state="locked",
            write_projects=[{"id": "other", "name": "Assays"}],
            touched_projects=[{"id": "other", "name": "Assays"}],
            write_project={"id": "other", "name": "Assays"},
        )
    )
    routes = _profile_api()

    result = runner.invoke(app, ["projects", "profile", "setup", "--project", P, "--message", "hi"])

    assert result.exit_code == 3, result.output
    assert "cannot write Helios" in " ".join(result.output.split())
    assert not routes["run"].called


@respx.mock
def test_session_show_after_ending_this_terminals_session_says_it_ended():
    scope_session.save_state(TokenState(SID, "bk_st_kept", _later()))
    scope_session.remember_terminal(SID, mode="auto_lock")
    ended = _session("ended", ended_at="2026-11-12T09:41:07+00:00")
    respx.post(f"{API}/v1/sessions/{SID}/end").respond(json=ended)
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=ended)

    assert runner.invoke(app, ["session", "end"]).exit_code == 0
    result = runner.invoke(app, ["session", "show"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == (
        f"This terminal's session {SID} ended at 2026-11-12 09:41 UTC. "
        "Start a new one with `beakr session start`."
    )
    as_json = runner.invoke(app, ["session", "show", "--json"])
    assert as_json.exit_code == 0 and json.loads(as_json.stdout)["status"] == "ended"


@respx.mock
def test_session_show_with_no_session_anywhere_still_asks_for_one():
    result = runner.invoke(app, ["session", "show"])
    assert result.exit_code == 2
