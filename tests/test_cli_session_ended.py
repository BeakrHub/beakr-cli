"""A shell whose session ended, or that names a project outside it.

What each piece promises, one test or more each:

- ``beakr session ...`` resolves project names with the person's own credential,
  never the shell's session token (which may have ended, or not read the project);
- a command in a shell whose session ended says so and how to leave it, never
  "run ``beakr auth login``" (self-hosted installations have no browser sign-in):
  from the API's ``401`` to a session token, and from renewing the token;
- a session whose token alone expired is renewed for the next command;
- the write gate refuses a write in an ended named session the same way;
- a project name the session does not read, but the person can, is said to be
  outside the session, with how to add it;
- the API's ``403 session_write_scope`` names projects, not ids, and only the
  refused ones.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import scope_session
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import TokenState

pytestmark = pytest.mark.real_project_refs

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
C = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
NAMES = {A: "Helios", B: "Assays", C: "Session C"}
PERSON_KEY = "bk_live_person_key_for_tests"
SHELL_TOKEN = "bk_st_shell"
PICKER = f"https://app.test/sessions/{SID}"
ENDED = f"This shell's session {SID} has ended."
LEAVE = (
    f"Run `unset {scope_session.SESSION_ENV}` to leave it, or `beakr session start` to begin a "
    "new one."
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def shell(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    monkeypatch.setattr("beakr_cli.project_ref._resolved", {})
    scope_session.save_state(TokenState(SID, SHELL_TOKEN, _later()))


def _later(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _session(status: str = "active", *, read=(A, B), write=(A,)) -> dict:
    return {
        "id": SID,
        "status": status,
        "folder": None,
        "remember_folder": False,
        "read_projects": [{"id": p, "name": NAMES[p]} for p in read],
        "write_projects": [{"id": p, "name": NAMES[p]} for p in write],
        "picker_url": PICKER,
    }


def _projects(*ids: str) -> list[dict]:
    return [{"id": p, "name": NAMES[p], "slug": NAMES[p].lower().replace(" ", "-")} for p in ids]


def _refusal(
    status: int, code: str, message: str, fix: str | None = None, **details
) -> httpx.Response:
    """The API's error envelope (``error``, and FastAPI's ``detail``)."""
    error = {"code": code, "message": message, "fix": fix}
    if details:
        error["details"] = details
    return httpx.Response(
        status, json={"detail": {"code": code, "message": message}, "error": error}
    )


def _token_refused() -> httpx.Response:
    return _refusal(
        401,
        "unauthorized",
        "Invalid, expired or ended session token",
        "Reconnect the MCP server, run `beakr auth login`, or use a current API key",
    )


def _by_credential(*, person: object, session: object):
    """A route answering the person's key and the shell's session token differently."""

    def answer(request: httpx.Request) -> httpx.Response:
        sent = request.headers["authorization"]
        chosen = session if sent == f"Bearer {SHELL_TOKEN}" else person
        return chosen if isinstance(chosen, httpx.Response) else httpx.Response(200, json=chosen)

    return answer


def _bearers(route) -> list[str]:
    return [call.request.headers["authorization"] for call in route.calls]


def _text(result) -> str:
    """The output as one line, without the box Click draws around a usage error."""
    return " ".join(result.output.replace("│", " ").split())


# --- The session commands use the person's credential ----------------------------------


@respx.mock
def test_session_start_resolves_names_with_the_persons_credential_in_an_ended_shell(
    tmp_path, monkeypatch
):
    # The picked lists are checked against the projects' boundaries first.
    respx.post(f"{API}/v1/sessions/options").respond(json={"valid": True, "reasons": []})
    monkeypatch.chdir(tmp_path)
    listed = respx.get(f"{API}/v1/projects").mock(
        side_effect=_by_credential(person=_projects(A, B, C), session=_token_refused())
    )
    created = respx.post(f"{API}/v1/sessions").respond(
        201,
        json={
            **_session(read=(A, B), write=(B,)),
            "id": "0f0e0d0c-0b0a-4908-8706-050403020100",
            "token": {"token": "bk_st_new", "token_type": "bearer", "expires_at": _later()},
        },
    )

    result = runner.invoke(
        app,
        ["session", "start", "-r", "Helios", "-w", "Assays", "--label", "cli test", "--env"],
    )

    assert result.exit_code == 0, result.output
    assert set(_bearers(listed)) == {f"Bearer {PERSON_KEY}"}
    assert _bearers(created) == [f"Bearer {PERSON_KEY}"]
    sent = json.loads(created.calls.last.request.content)
    assert sent["read_project_ids"] == [A, B] and sent["write_project_ids"] == [B]
    assert "auth login" not in result.output


@respx.mock
def test_session_edit_resolves_a_project_the_session_does_not_read():
    # The picked lists are checked against the projects' boundaries first.
    respx.post(f"{API}/v1/sessions/options").respond(json={"valid": True, "reasons": []})
    listed = respx.get(f"{API}/v1/projects").mock(
        side_effect=_by_credential(person=_projects(A, B, C), session=_projects(A, B))
    )
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    patched = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session(read=(A, C)))

    result = runner.invoke(app, ["session", "edit", "-r", "Helios", "-r", "Session C", "--json"])

    assert result.exit_code == 0, result.output
    assert set(_bearers(listed)) == {f"Bearer {PERSON_KEY}"}
    assert json.loads(patched.calls.last.request.content)["read_project_ids"] == [A, C]


# --- A command in a shell whose session ended ----------------------------------------


@respx.mock
def test_a_command_in_an_ended_session_says_so_not_sign_in():
    respx.get(f"{API}/v1/projects").mock(return_value=_token_refused())
    looked = respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("ended"))

    result = runner.invoke(app, ["projects", "ls"])

    assert result.exit_code == 1
    assert f"{ENDED} To fix: {LEAVE}" in _text(result)
    assert "auth login" not in result.output
    assert _bearers(looked) == [f"Bearer {PERSON_KEY}"]


@respx.mock
def test_an_ended_session_under_json_is_a_session_ended_error():
    respx.get(f"{API}/v1/projects").mock(return_value=_token_refused())
    respx.get(f"{API}/v1/sessions/{SID}").respond(404, json={"error": {"code": "not_found"}})

    result = runner.invoke(app, ["projects", "ls", "--json"])

    error = json.loads(result.stdout)["error"]
    assert error["code"] == "session_ended"
    assert error["message"] == ENDED and error["fix"] == LEAVE


@respx.mock
def test_a_project_name_in_an_ended_session_says_so():
    respx.get(f"{API}/v1/projects").mock(
        side_effect=_by_credential(person=_projects(A, B, C), session=_token_refused())
    )
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("ended"))

    result = runner.invoke(app, ["kb", "ls", "-P", "Helios"])

    assert result.exit_code == 1
    assert ENDED in _text(result) and "auth login" not in result.output


@respx.mock
def test_renewing_the_token_of_an_ended_session_says_so():
    scope_session.save_state(TokenState(SID, SHELL_TOKEN, _later(30)))
    respx.post(f"{API}/v1/sessions/{SID}/tokens").mock(
        return_value=_refusal(409, "session_ended", "This session has ended.")
    )
    listed = respx.get(f"{API}/v1/projects").respond(json=[])

    result = runner.invoke(app, ["projects", "ls"])

    assert result.exit_code == 1
    assert f"{ENDED} To fix: {LEAVE}" in _text(result)
    assert not listed.called


@respx.mock
def test_an_open_session_whose_token_was_refused_is_renewed_for_the_next_command():
    respx.get(f"{API}/v1/projects").mock(return_value=_token_refused())
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    renewed = respx.post(f"{API}/v1/sessions/{SID}/tokens").respond(
        201, json={"token": "bk_st_renewed", "token_type": "bearer", "expires_at": _later()}
    )

    result = runner.invoke(app, ["projects", "ls"])

    assert result.exit_code == 1
    assert "had expired; it has been renewed. To fix: Run the command again." in _text(result)
    assert _bearers(renewed) == [f"Bearer {PERSON_KEY}"]
    assert scope_session.load_state(SID).token == "bk_st_renewed"


@respx.mock
def test_the_persons_own_refused_credential_still_suggests_signing_in(monkeypatch):
    monkeypatch.delenv(scope_session.SESSION_ENV)
    respx.get(f"{API}/v1/projects").mock(
        return_value=_refusal(401, "unauthorized", "Invalid API key")
    )

    result = runner.invoke(app, ["projects", "ls"])

    assert result.exit_code == 3
    assert "beakr auth login" in result.output


@pytest.mark.real_write_sessions
@respx.mock
def test_a_write_in_an_ended_named_session_is_refused_the_same_way(monkeypatch):
    from beakr_cli import write_session

    monkeypatch.setattr(write_session, "_interactive", lambda: False)
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("ended"))
    started = respx.post(f"{API}/v1/sessions").respond(201, json={})
    deleted = respx.delete(f"{API}/v1/artifacts/f-1").respond(204)

    result = runner.invoke(app, ["files", "rm", "f-1", "--yes"])

    assert result.exit_code == 1
    assert f"This shell's session {SID} has ended; nothing was written. To fix: {LEAVE}" in _text(
        result
    )
    assert not deleted.called and not started.called


# --- A project outside the session ---------------------------------------------------


@respx.mock
def test_a_project_outside_the_session_is_named_as_outside_it():
    listed = respx.get(f"{API}/v1/projects").mock(
        side_effect=_by_credential(person=_projects(A, B, C), session=_projects(A, B))
    )
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())

    result = runner.invoke(app, ["kb", "ls", "-P", "Session C"])

    assert result.exit_code == 2
    text = _text(result)
    assert "'Session C' isn't one of this session's projects (it reads: Helios, Assays)." in text
    assert f"Add it with `beakr session edit {SID}` or at {PICKER}." in text
    assert "No project with id" not in text
    # The name was resolved with the session's token first, then the person's.
    assert _bearers(listed) == [f"Bearer {SHELL_TOKEN}", f"Bearer {PERSON_KEY}"]


@respx.mock
def test_a_name_nobody_can_see_is_still_not_found():
    respx.get(f"{API}/v1/projects").mock(
        side_effect=_by_credential(person=_projects(A, B), session=_projects(A, B))
    )
    looked = respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())

    result = runner.invoke(app, ["kb", "ls", "-P", "Nowhere"])

    assert result.exit_code == 2
    assert "No project with id, slug or name 'Nowhere'" in _text(result)
    assert not looked.called


# --- The API's write refusal, by name ------------------------------------------------


@respx.mock
def test_the_apis_write_refusal_outside_the_gate_names_the_projects():
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    respx.post(f"{API}/v1/projects").mock(
        return_value=_refusal(
            403,
            "session_write_scope",
            f"This session may read but not write {B}. It may write {A}. The person can "
            f"change that at {PICKER}.",
            session_id=SID,
            picker_url=PICKER,
            write_project_ids=[A],
        )
    )

    result = runner.invoke(app, ["projects", "create", "New one"])

    assert result.exit_code == 3
    text = _text(result)
    assert f"Session {SID} may not write to Assays; nothing was written." in text
    assert "It may write: Helios." in text
    assert B not in text and A not in text


@pytest.mark.real_write_sessions
@respx.mock
def test_the_gates_write_refusal_names_only_the_refused_project(monkeypatch):
    from beakr_cli import write_session

    monkeypatch.setattr(write_session, "_interactive", lambda: False)
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    respx.delete(f"{API}/v1/artifacts/f-1").mock(
        return_value=_refusal(
            403,
            "session_write_scope",
            f"This session may read but not write {B}. It may write {A}.",
        )
    )

    result = runner.invoke(app, ["files", "rm", "f-1", "--yes"])

    assert result.exit_code == 3
    # The API's message names the write list too; only Assays was refused.
    assert f"Session {SID} may not write to Assays; nothing was written." in _text(result)
