"""``beakr projects profile setup``: the profile-setup conversation in a terminal.

The CLI starts the same setup run the web app's docked editor does, marked for
a client with no panel and held to the profile, streams the agent's replies,
shows what each save wrote (entity types first), answers a question the run
parked on, and ends on an empty line. A project that owes its required profile
is refused files and Knowledge by the engine (409 project_profile_required);
the CLI says how to set it up.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "11111111-1111-4111-8111-111111111111"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _sse(*events: dict) -> httpx.Response:
    body = "".join(f"id: {i}\ndata: {json.dumps(e)}\n\n" for i, e in enumerate(events, 1))
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _invoke(*args: str, input: str = ""):
    return runner.invoke(
        app, list(args), input=input, env={"BEAKR_OUTPUT": "human", "COLUMNS": "200"}
    )


@respx.mock
def test_setup_runs_the_conversation_and_shows_the_saved_profile(monkeypatch) -> None:
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    respx.get(f"{API}/v1/projects/{PROJECT}").mock(
        side_effect=[
            httpx.Response(
                200, json={"id": PROJECT, "name": "Basel", "requires_knowledge_profile": True}
            ),
            httpx.Response(
                200, json={"id": PROJECT, "name": "Basel", "requires_knowledge_profile": False}
            ),
        ]
    )
    starts = respx.post(f"{API}/v1/runs").mock(
        side_effect=[
            httpx.Response(200, json={"run_id": "r1", "thread_id": "t1"}),
            httpx.Response(200, json={"run_id": "r2", "thread_id": "t1"}),
        ]
    )
    respx.get(f"{API}/v1/runs/r1/events").mock(
        return_value=_sse(
            {"type": "content_delta", "data": {"delta": "What does one lot hold?"}},
            {"type": "stream_complete", "data": {"status": "interrupted"}},
        )
    )
    respx.get(f"{API}/v1/runs/r1").respond(
        200,
        json={
            "run_id": "r1",
            "thread_id": "t1",
            "status": "interrupted",
            "question": {"text": "Which types?", "options": ["lot, deviation", "batch"]},
        },
    )
    respx.get(f"{API}/v1/runs/r2/events").mock(
        return_value=_sse(
            {
                "type": "profile_update",
                "data": {"entity_types": ["lot", "deviation"], "goals": ["Close deviations"]},
            },
            {"type": "content_delta", "data": {"delta": "Saved your types."}},
            {"type": "stream_complete", "data": {"status": "completed"}},
        )
    )
    respx.get(f"{API}/v1/runs/r2").respond(
        200, json={"run_id": "r2", "thread_id": "t1", "status": "completed"}
    )
    respx.get(f"{API}/v1/projects/{PROJECT}/knowledge-profile").respond(
        200, json={"project_id": PROJECT, "entity_types": ["lot", "deviation"], "goals": []}
    )

    result = _invoke("projects", "profile", "setup", "-P", PROJECT, input="1\n\n")

    assert result.exit_code == 0, result.output
    assert "What does one lot hold?" in result.output
    assert "Which types?" in result.output
    assert "Saved entity types: lot, deviation; goals (1)" in result.output
    assert "Entity types: lot, deviation" in result.output

    first = json.loads(starts.calls[0].request.content)
    assert first["home_project_id"] == PROJECT
    assert first["profile_builder_meta"] == {
        "profile_scope": "project",
        "project_id": PROJECT,
        "required": True,
        "setup_only": True,
        "surface": "terminal",
    }
    assert "thread_id" not in first and "is_ask_user_response" not in first
    # The numbered option answered the parked question, in the same thread.
    second = json.loads(starts.calls[1].request.content)
    assert second["message"] == "lot, deviation"
    assert second["thread_id"] == "t1"
    assert second["is_ask_user_response"] is True


@respx.mock
def test_a_write_refused_for_a_missing_profile_says_how_to_set_it_up(tmp_path) -> None:
    respx.put(f"{API}/v1/projects/{PROJECT}/knowledge-profile").respond(
        409,
        json={
            "error": {
                "code": "project_profile_required",
                "message": "Project 'Basel' needs a Knowledge profile.",
                "details": {"project_id": PROJECT, "project_name": "Basel"},
            }
        },
    )
    path = tmp_path / "profile.yaml"
    path.write_text("description: x\n")
    result = _invoke("projects", "profile", "set", "-P", PROJECT, "--file", str(path))
    assert result.exit_code == 5
    assert f"beakr projects profile setup -P {PROJECT}" in result.output


@respx.mock
def test_without_a_terminal_it_runs_one_turn_and_says_how_to_reply(monkeypatch) -> None:
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: False)
    respx.get(f"{API}/v1/projects/{PROJECT}").respond(
        200, json={"id": PROJECT, "name": "Basel", "requires_knowledge_profile": False}
    )
    start = respx.post(f"{API}/v1/runs").respond(200, json={"run_id": "r1", "thread_id": "t1"})
    respx.get(f"{API}/v1/runs/r1/events").mock(
        return_value=_sse(
            {"type": "content_delta", "data": {"delta": "Which kinds of things?"}},
            {"type": "stream_complete", "data": {"status": "interrupted"}},
        )
    )
    respx.get(f"{API}/v1/runs/r1").respond(
        200,
        json={
            "run_id": "r1",
            "thread_id": "t1",
            "status": "interrupted",
            "question": {"text": "Which types?", "options": []},
        },
    )
    respx.get(f"{API}/v1/projects/{PROJECT}/knowledge-profile").respond(
        404, json={"error": {"code": "knowledge_profile_not_found", "message": "None yet."}}
    )

    result = _invoke(
        "projects",
        "profile",
        "setup",
        "-P",
        PROJECT,
        "--thread",
        "t0",
        "--answer",
        "-m",
        "Lots and deviations",
    )

    assert result.exit_code == 0, result.output
    body = json.loads(start.calls.last.request.content)
    assert body["thread_id"] == "t0"
    assert body["is_ask_user_response"] is True
    assert body["message"] == "Lots and deviations"
    assert "--thread t1 --answer" in result.output
