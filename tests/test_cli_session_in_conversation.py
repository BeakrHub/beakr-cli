"""``beakr session`` inside an agent conversation reads only that conversation's session.

The session commands act with the person's own credential, which reads every
session of theirs unredacted; a read trail lists other projects' reads. Run by an
agent (or a ``!`` command, which shares its environment), reading another session
would carry that into the conversation: the same leak the engine closed for session
tokens (#2636). Inside an agent conversation:

- ``show`` and ``trail`` read through the conversation session's own token, and
  name no other session (refused here, exit 3, without a request);
- ``ls`` lists through that token, which the API answers with that session only;
- ``end`` of another session is refused; ending its own still works.

From a plain terminal everything acts as the person, as before.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import scope_session
from beakr_cli.main import internal_app as app

pytestmark = pytest.mark.real_conversation_sessions

API = "https://api.test"
OWN = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
OTHER = "2f3e4d5c-6b7a-4c8d-9e0f-1a2b3c4d5e6f"
PERSON = "bk_live_person"
CONVERSATION = "conv-123"
TRAIL = {"data": [], "next_cursor": None, "has_more": False}

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)


@pytest.fixture
def conversation(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CONVERSATION)


def _session(sid: str = OWN, **extra) -> dict:
    return {
        "id": sid,
        "status": "active",
        "mode": "auto_lock",
        "lock_state": "open",
        "agent_session": CONVERSATION if sid == OWN else None,
        "read_projects": [],
        "write_projects": [],
        "touched_projects": [],
        "picker_url": f"https://app.test/sessions/{sid}",
        **extra,
    }


def _join() -> respx.Route:
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return respx.post(f"{API}/v1/sessions").respond(
        201,
        json={**_session(), "token": {"token": "bk_st_conv", "expires_at": later}},
    )


def _bearer(route: respx.Route) -> str:
    return route.calls.last.request.headers["authorization"]


def _text(result) -> str:
    return " ".join(result.output.split())


# --- Inside an agent conversation ---------------------------------------------------------


@respx.mock
def test_show_reads_the_conversations_own_session_with_its_token(conversation):
    joined = _join()
    own = respx.get(f"{API}/v1/sessions/{OWN}").respond(json=_session())

    result = runner.invoke(app, ["session", "show"])

    assert result.exit_code == 0, result.output
    assert json.loads(joined.calls.last.request.content)["agent_session"] == CONVERSATION
    assert _bearer(own) == "Bearer bk_st_conv"


@pytest.mark.parametrize("command", ["show", "trail"])
@respx.mock
def test_another_session_is_refused_without_a_request(conversation, command):
    _join()
    other = respx.route(path__startswith=f"/v1/sessions/{OTHER}").respond(json=_session(OTHER))

    result = runner.invoke(app, ["session", command, OTHER])

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert f"reads only this conversation's session ({OWN}); {OTHER} is another" in text
    assert "a terminal outside the agent conversation" in text
    assert "`!` in Claude Code" in text
    assert not other.called


@respx.mock
def test_trail_reads_its_own_session_with_its_token(conversation):
    _join()
    trail = respx.get(f"{API}/v1/sessions/{OWN}/trail").respond(json=TRAIL)

    result = runner.invoke(app, ["session", "trail"])

    assert result.exit_code == 0, result.output
    assert _bearer(trail) == "Bearer bk_st_conv"


@respx.mock
def test_ls_lists_through_the_conversations_token(conversation):
    _join()
    listed = respx.get(f"{API}/v1/sessions").respond(json={"data": [_session()]})

    result = runner.invoke(app, ["session", "ls", "--json"])

    assert result.exit_code == 0, result.output
    assert _bearer(listed) == "Bearer bk_st_conv"
    assert [row["id"] for row in json.loads(result.stdout)] == [OWN]


@respx.mock
def test_ls_of_an_ended_conversation_keeps_only_its_own_row(conversation):
    respx.post(f"{API}/v1/sessions").respond(
        409, json={"error": {"code": "session_ended", "message": "This agent session has ended."}}
    )
    respx.get(f"{API}/v1/sessions").respond(
        json={"data": [_session(status="ended"), _session(OTHER)]}
    )

    result = runner.invoke(app, ["session", "ls", "--all", "--json"])

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.stdout)] == [OWN]


@respx.mock
def test_ending_another_session_is_refused_and_its_own_still_ends(conversation):
    _join()
    other = respx.post(f"{API}/v1/sessions/{OTHER}/end").respond(json=_session(OTHER))
    own = respx.post(f"{API}/v1/sessions/{OWN}/end").respond(json=_session(status="ended"))

    refused = runner.invoke(app, ["session", "end", OTHER])
    assert refused.exit_code == 3, refused.output
    assert "End other sessions from the web app or a terminal outside" in _text(refused)
    assert not other.called

    ended = runner.invoke(app, ["session", "end"])
    assert ended.exit_code == 0, ended.output
    assert own.called


# --- A plain terminal: as the person, as before -------------------------------------------


@respx.mock
def test_a_plain_terminal_reads_any_of_the_persons_sessions():
    shown = respx.get(f"{API}/v1/sessions/{OTHER}").respond(json=_session(OTHER))
    trail = respx.get(f"{API}/v1/sessions/{OTHER}/trail").respond(json=TRAIL)
    listed = respx.get(f"{API}/v1/sessions").respond(json={"data": [_session(), _session(OTHER)]})
    ended = respx.post(f"{API}/v1/sessions/{OTHER}/end").respond(json=_session(OTHER))
    joined = respx.post(f"{API}/v1/sessions").respond(201, json=_session())

    for args in (["show", OTHER], ["trail", OTHER], ["ls", "--json"], ["end", OTHER]):
        result = runner.invoke(app, ["session", *args])
        assert result.exit_code == 0, (args, result.output)

    for route in (shown, trail, listed, ended):
        assert _bearer(route) == f"Bearer {PERSON}"
    assert not joined.called
