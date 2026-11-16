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


# --- A person's own acts are refused inside an agent conversation ------------------------

PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
ORG = "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"


@pytest.mark.parametrize(
    ("argv", "what", "where"),
    [
        # `beakr session start` declares the conversation's own session instead
        # (tests/test_project_boundaries.py); `beakr session edit` is below.
        (
            ["org", "isolation", "set", "--org", ORG, "--guard", "allow", "--yes"],
            "beakr org isolation set",
            "Change project isolation",
        ),
        (
            ["org", "read-trail", "set", "--org", ORG, "--days", "1", "--yes"],
            "beakr org read-trail set",
            "Change the read trail",
        ),
        (
            ["projects", "guard", "set", PROJECT, "allow", "--yes"],
            "beakr projects guard set",
            "Change a project's guard",
        ),
        (
            ["kb", "agent-review", "set", "--uploads", "off"],
            "beakr kb agent-review set",
            "Change agent review",
        ),
        (["files", "approve", "f-1"], "beakr files approve", "Approve the file"),
    ],
)
@respx.mock
def test_a_persons_own_act_is_refused_inside_an_agent_conversation(conversation, argv, what, where):
    api = respx.route(host="api.test").respond(200, json={})

    result = runner.invoke(app, argv)

    assert result.exit_code == 3, (argv, result.output)
    text = _text(result)
    assert f"`{what}` is refused inside an agent conversation ({CONVERSATION})" in text
    assert f"{where} in the web app or a terminal outside the agent conversation" in text
    assert "`!` in Claude Code" in text
    assert not api.called


# --- Only the person widens a session -----------------------------------------------------
#
# Root cause (15 Nov, fdoctant): inside an agent conversation the agent read the person's
# key, and `beakr api sessions update-session` widened its session from one project to six
# (`beakr session edit` failed only on resolving a name, before its own refusal ran). The API
# now keeps a widening of an agent's session by a key or token as a proposal for the person
# (403 session_widen_needs_person, with the link); the CLI prints that, and itself refuses,
# before any name is resolved, what the API would not stop: starting a session, minting a
# session's token, or changing a session other than the conversation's own.

OWN_PAGE = f"https://app.test/sessions/{OWN}"
LEADERSHIP = "01cd300f-a774-4df8-91f5-a2e7d417e6b4"
PROPOSED = "Only the person can widen this agent session; the request is kept as a proposal."


def _own_session(**extra) -> None:
    """The conversation's session, as the person's key lists and reads it."""
    session = _session(
        **{
            "mode": "picked",
            "read_projects": [{"id": PROJECT, "name": "Internal"}],
            "write_projects": [],
            **extra,
        }
    )
    respx.get(f"{API}/v1/sessions").respond(json={"data": [session], "has_more": False})
    respx.get(f"{API}/v1/sessions/{OWN}").respond(json=session)


def _must_pick() -> None:
    """An organization where every session must pick: calls run with the person's key."""
    respx.get(f"{API}/v1/me/organization").respond(json={"id": ORG})
    respx.get(f"{API}/v1/organizations/{ORG}/project-isolation").respond(
        json={"session_mode": "must_pick", "available": {"server_agent_sessions": True}}
    )


def _needs_person() -> dict:
    return {
        "error": {
            "code": "session_widen_needs_person",
            "message": PROPOSED,
            "details": {"picker_url": OWN_PAGE, "proposed_read": [PROJECT, LEADERSHIP]},
        }
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["session", "edit", OWN, "--read-everything"],
        ["session", "edit", "--read", PROJECT, "--read", LEADERSHIP],
        [
            "api",
            "sessions",
            "update-session",
            OWN,
            "--data",
            json.dumps({"read_project_ids": [PROJECT, LEADERSHIP], "write_project_ids": []}),
        ],
    ],
)
@respx.mock
def test_widening_the_conversations_session_is_proposed_with_the_persons_link(conversation, argv):
    _own_session()
    _must_pick()
    respx.post(f"{API}/v1/sessions/options").respond(json={"valid": True, "reasons": []})
    proposed = respx.patch(f"{API}/v1/sessions/{OWN}").respond(403, json=_needs_person())

    result = runner.invoke(app, argv)

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert PROPOSED in text
    assert f"Confirm it in your browser: {OWN_PAGE}" in text
    assert proposed.called


@pytest.mark.parametrize(
    ("argv", "what"),
    [
        (["session", "edit", OTHER, "--read", "Leadership"], "beakr session edit"),
        (
            [
                "api",
                "sessions",
                "update-session",
                OTHER,
                "--data",
                json.dumps({"read_project_ids": [PROJECT], "write_project_ids": []}),
            ],
            "beakr api sessions update-session",
        ),
        (
            [
                "api",
                "sessions",
                "create-session",
                "--data",
                json.dumps({"read_project_ids": [LEADERSHIP], "write_project_ids": []}),
            ],
            "beakr api sessions create-session",
        ),
        (
            ["api", "sessions", "mint-session-token", OTHER, "--data", "{}"],
            "beakr api sessions mint-session-token",
        ),
    ],
)
@respx.mock
def test_what_the_api_would_not_stop_is_refused_inside_a_conversation(conversation, argv, what):
    _own_session()
    listed = respx.get(f"{API}/v1/projects")
    changed = respx.patch(url__regex=rf"{API}/v1/sessions/.*")
    created = respx.post(url__regex=rf"{API}/v1/sessions.*")

    result = runner.invoke(app, argv)

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert f"`{what}` is refused inside an agent conversation ({CONVERSATION})" in text
    assert f"only a person can widen this session: {OWN_PAGE}" in text
    assert not changed.called and not created.called and not listed.called


@respx.mock
def test_a_change_that_only_narrows_the_conversations_session_goes_through(conversation):
    _own_session(
        read_projects=[{"id": PROJECT, "name": "Internal"}, {"id": LEADERSHIP, "name": "Lead"}]
    )
    _must_pick()
    changed = respx.patch(f"{API}/v1/sessions/{OWN}").respond(json=_session())

    result = runner.invoke(
        app,
        [
            "api",
            "sessions",
            "update-session",
            OWN,
            "--data",
            json.dumps({"read_project_ids": [PROJECT], "write_project_ids": []}),
        ],
    )

    assert result.exit_code == 0, result.output
    assert changed.called


@respx.mock
def test_a_person_at_a_terminal_widening_an_agent_session_gets_the_link(monkeypatch):
    # (d): outside any conversation the person's key may not widen an agent's session
    # either; the API's message and the link are printed as they came.
    respx.get(f"{API}/v1/sessions/{OWN}").respond(json=_session())
    respx.patch(f"{API}/v1/sessions/{OWN}").respond(403, json=_needs_person())

    result = runner.invoke(app, ["session", "edit", OWN, "--read-everything"])

    assert result.exit_code == 3, result.output
    assert f"Confirm it in your browser: {OWN_PAGE}" in _text(result)
