"""A restarted `beakr mcp` rejoins its conversation's session without re-declaring it.

Root cause (16 Nov, 1.0.2): McpSession.start sent the folder's remembered lists with
every start, restarts included. The API returns a conversation's unended session to a
plain POST /v1/sessions {agent_session}, but lists sent with it change the session:
once it has touched a project that is refused (409 session_already_started), and
wider lists are 403 session_widen_needs_person, so the restarted server's tools
stopped working. Now the join is plain, and the remembered lists are offered only to a
session that has not started (untouched, automatic or pending).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from beakr_cli import scope_session
from beakr_cli.scope_session import McpSession

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
TOKEN = {"token": "bk_st_x", "expires_at": "2099-01-01T00:00:00+00:00"}


@pytest.fixture(autouse=True)
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person")
    monkeypatch.setattr(scope_session, "remembered_selection", lambda folder: ([A, B], [A]))


def _session(**extra) -> dict:
    return {
        "id": SID,
        "status": "active",
        "mode": "automatic",
        "agent_session": "conv-1",
        "read_projects": [],
        "write_projects": [],
        "touched_projects": [],
        "token": TOKEN,
        **extra,
    }


def _bodies(route) -> list[dict]:
    return [json.loads(call.request.content) for call in route.calls]


@respx.mock
def test_a_restart_joins_a_touched_session_without_sending_lists(tmp_path):
    touched = _session(mode="picked", touched_projects=[{"id": A, "name": "Helios"}])
    posted = respx.post(f"{API}/v1/sessions").respond(201, json=touched)
    held = McpSession(folder=str(tmp_path), agent="conv-1")

    assert asyncio.run(held.start())

    bodies = _bodies(posted)
    assert len(bodies) == 1
    assert bodies[0]["agent_session"] == "conv-1"
    assert "read_project_ids" not in bodies[0] and "read_everything" not in bodies[0]
    assert held.id == SID and held.session["touched_projects"]


@respx.mock
def test_a_new_session_still_gets_the_folders_remembered_lists(tmp_path):
    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "read_project_ids" in body:
            return httpx.Response(201, json=_session(mode="picked", read_projects=[{"id": A}]))
        return httpx.Response(201, json=_session())

    posted = respx.post(f"{API}/v1/sessions").mock(side_effect=answer)
    held = McpSession(folder=str(tmp_path), agent="conv-1")

    assert asyncio.run(held.start())

    bodies = _bodies(posted)
    assert "read_project_ids" not in bodies[0]
    assert bodies[1]["read_project_ids"] == [A, B] and bodies[1]["write_project_ids"] == [A]
    assert held.session["mode"] == "picked"


@respx.mock
def test_in_must_pick_the_remembered_lists_become_a_proposal_and_the_session_is_kept(tmp_path):
    pending = _session(status="pending", mode="picked")

    def answer(request: httpx.Request) -> httpx.Response:
        if "read_project_ids" in json.loads(request.content):
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": "session_widen_needs_person",
                        "message": "Only a person can choose this agent's session's projects.",
                        "details": {"picker_url": f"https://app.test/sessions/{SID}"},
                    }
                },
            )
        return httpx.Response(201, json=pending)

    respx.post(f"{API}/v1/sessions").mock(side_effect=answer)
    held = McpSession(folder=str(tmp_path), agent="conv-1")

    assert asyncio.run(held.start())

    assert held.id == SID and held.pending


@respx.mock
def test_without_a_conversation_id_the_lists_go_with_the_one_request(tmp_path):
    posted = respx.post(f"{API}/v1/sessions").respond(
        201, json=_session(mode="picked", agent_session=None)
    )
    held = McpSession(folder=str(tmp_path), agent=None)

    assert asyncio.run(held.start())

    bodies = _bodies(posted)
    assert len(bodies) == 1 and bodies[0]["read_project_ids"] == [A, B]
