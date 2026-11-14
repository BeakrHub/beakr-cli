"""Every CLI client names its ``X-Beakr-Session`` itself, never the SDK's made-up id.

The bug (rc.8, with ``server_agent_sessions`` on): the Python SDK gives each client
it builds a fresh ``sdk-python-<random>`` session id unless it is told one. The API
joins a person's API key that sends ``X-Beakr-Session`` to that conversation's
session, so every CLI process ran inside a throwaway session of its own: after
``beakr session start --env``, ``beakr session show`` answered ``404`` and an
upload said "this shell's session has ended".

What this promises, one test or more each:

- no CLI request carries an ``sdk-python-*`` session, whatever the SDK's default;
- in a plain terminal, ``session start --env``, then ``session show`` and an upload
  in that shell work against an API that joins every session header it sees;
- the person's own credential (the session commands, starting and checking a
  session) carries no ``X-Beakr-Session`` even in an agent conversation; other
  requests there carry the conversation's id;
- "this shell's session has ended" is said only for a ``404`` to the person's own
  credential sent without a session header.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from beakr_sdk import _base_client
from typer.testing import CliRunner

from beakr_cli import scope_session, write_session
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import TokenState
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
PERSON_KEY = "bk_live_person_key_for_tests"
MADE_UP = "sdk-python-0123456789abcdef0123456789abcdef"

runner = CliRunner()


@pytest.fixture(autouse=True)
def sdk_makes_up_sessions(monkeypatch):
    """The SDK as kyle-sdk's per-client default builds it: a made-up id unless told one.

    The locked SDK predates that default, so it is put in here; a CLI client that
    leaves ``session`` unset then sends ``MADE_UP``, as it did in rc.8.
    """
    original = _base_client.BaseClient.__init__

    def init(self, *args, session=None, **kwargs):
        original(self, *args, session=MADE_UP if session is None else session, **kwargs)

    monkeypatch.setattr(_base_client.BaseClient, "__init__", init)
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)
    monkeypatch.setattr(write_session, "ENABLED", True)
    monkeypatch.setattr(write_session, "_interactive", lambda: False)


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def _session() -> dict:
    return {
        "id": SID,
        "status": "active",
        "mode": "picker",
        "agent_session": None,
        "client_name": "beakr-cli",
        "label": "beakr shell in work",
        "folder": None,
        "remember_folder": False,
        "read_projects": [{"id": A, "name": "Helios"}],
        "write_projects": [{"id": A, "name": "Helios"}],
        "touched_projects": [],
        "picker_url": f"https://app.test/sessions/{SID}",
        "run_label": f"session:{SID}",
    }


def _joining_api(route: respx.Route) -> None:
    """The session routes of an API with ``server_agent_sessions`` on.

    A person's key sending ``X-Beakr-Session`` is joined to that conversation's own
    session, where the shell's session is not found: ``404``, as rc.8 answered.
    """

    def answer(request: httpx.Request) -> httpx.Response:
        if "x-beakr-session" in request.headers:
            return httpx.Response(
                404, json={"error": {"code": "not_found", "message": "Session not found."}}
            )
        return httpx.Response(200, json=_session())

    route.mock(side_effect=answer)


def _sent_session_headers() -> list[str]:
    return [call.request.headers.get("x-beakr-session", "") for call in respx.calls]


@respx.mock
def test_a_shell_session_works_start_show_and_upload(tmp_path, monkeypatch):
    # The picked lists are checked against the projects' boundaries first.
    respx.post(f"{API}/v1/sessions/options").respond(json={"valid": True, "reasons": []})
    respx.post(f"{API}/v1/sessions").respond(
        201,
        json={
            **_session(),
            "token": {"token": "bk_st_shell", "token_type": "bearer", "expires_at": _later()},
        },
    )
    _joining_api(respx.get(f"{API}/v1/sessions/{SID}"))
    uploads = mock_resumable_uploads(API)

    started = runner.invoke(app, ["session", "start", "--read", A, "--write", A, "--env"])
    assert started.exit_code == 0, started.output
    assert started.stdout.strip() == f"export {scope_session.SESSION_ENV}={SID}"
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)

    shown = runner.invoke(app, ["session", "show"])
    assert shown.exit_code == 0, shown.output
    assert "Helios" in shown.output

    path = tmp_path / "notes.md"
    path.write_text("hello")
    uploaded = runner.invoke(app, ["upload", str(path), "--project", A, "--json"])
    assert uploaded.exit_code == 0, uploaded.output
    assert "has ended" not in uploaded.output
    created = uploads["create"].calls.last.request
    assert created.headers["authorization"] == "Bearer bk_st_shell"

    sent = _sent_session_headers()
    assert sent and not any(h.startswith("sdk-python-") for h in sent)
    # A plain terminal names no agent conversation: no session header at all.
    assert not any(sent)


@respx.mock
def test_in_an_agent_conversation_only_its_id_is_sent_and_never_with_the_persons_key(
    monkeypatch,
):
    from beakr_cli.sdk import get_sdk

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-123")
    me = respx.get(f"{API}/v1/me").respond(json={"user": {}})
    with get_sdk() as sdk:
        sdk.me.get()
    assert me.calls.last.request.headers["x-beakr-session"] == "conv-123"
    with get_sdk(scoped=False) as person:
        person.me.get()
    assert "x-beakr-session" not in me.calls.last.request.headers


def test_a_404_from_inside_some_session_is_not_said_to_be_the_end_of_the_shells():
    """A 404 to a request that carried a session header is passed on as it is."""
    from beakr_sdk.errors import NotFoundError

    def refusal(headers: dict[str, str]) -> NotFoundError:
        request = httpx.Request("GET", f"{API}/v1/sessions/{SID}", headers=headers)
        exc = NotFoundError("Session not found.", status=404, code="not_found")
        exc.response = httpx.Response(404, request=request)
        return exc

    assert not scope_session.gone(refusal({"X-Beakr-Session": "conv-123"}))
    assert not scope_session.gone(refusal({"Authorization": "Bearer bk_st_shell"}))
    assert scope_session.gone(refusal({"Authorization": f"Bearer {PERSON_KEY}"}))


@respx.mock
def test_a_shell_session_that_really_ended_is_still_said_so(monkeypatch):
    scope_session.save_state(TokenState(SID, "bk_st_shell", _later()))
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    respx.get(f"{API}/v1/sessions/{SID}").respond(
        404, json={"error": {"code": "not_found", "message": "Session not found."}}
    )

    result = runner.invoke(app, ["files", "rm", "f-1", "--yes"])

    assert result.exit_code == 1, result.output
    assert f"This shell's session {SID} has ended" in " ".join(result.output.split())
    assert all(not h for h in _sent_session_headers())


def test_every_private_client_names_its_session():
    """A client built outside ``get_sdk`` (sign-in, update checks) sends none."""
    from beakr_cli._private_api import AsyncPrivateBeakr, PrivateBeakr

    assert not PrivateBeakr(api_key=PERSON_KEY, base_url=API).session
    assert not AsyncPrivateBeakr(api_key=PERSON_KEY, base_url=API).session
