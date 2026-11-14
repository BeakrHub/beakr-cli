"""Project isolation (Octant): auto_lock sessions, one session per agent conversation,
writes that need a session, and the cross-project guard (beakr-engine
``max/sh-project-isolation``).

What each piece promises, one test or more each:

- ``beakr mcp`` and the CLI join the agent conversation's session
  (``agent_session``): both hold a token for the same session; an installation
  without agent sessions starts the session on its own, as before;
- in auto_lock mode nothing asks: ``beakr mcp`` answers tools at once, the write
  gate writes without the picker, a remembered folder choice is not sent, and in
  an agent conversation the CLI's reads carry the conversation session's token too;
- picker mode is unchanged: a pending session still answers with the picker;
- a read-only or locked session's write is refused by name with the fix that
  works where the command runs (a new agent conversation, or ``beakr session end``);
- a write outside a session (``session_required``) and the guard's refusals say
  what to do and exit 3;
- a write the guard held is a normal result naming who must approve it;
- ``beakr session show`` and the ``session_status`` tool show the mode, the locked
  project, read-only and the touched projects;
- an agent conversation's ended session is never replaced: the agent is told to
  start a new conversation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import mcp_session, scope_session, write_session
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import McpSession, SessionScopeError, TokenState

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
NAMES = {A: "Pharma A", B: "Pharma B"}
PERSON_KEY = "bk_live_person_key_for_tests"
ORG = "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"
CONVERSATION = "conv-123"
FILE = "f-1"

pytestmark = pytest.mark.real_conversation_sessions

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)
    monkeypatch.delenv(scope_session.MCP_SESSIONS_ENV, raising=False)
    monkeypatch.setattr(write_session, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(write_session, "_interactive", lambda: False)
    monkeypatch.setattr("beakr_cli.project_binding.credential_org_id", lambda: ORG)
    scope_session.ACTIVE = None
    yield
    scope_session.ACTIVE = None


@pytest.fixture
def conversation(monkeypatch):
    """The command runs in a Claude Code conversation."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CONVERSATION)
    return CONVERSATION


def _later(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _project(pid: str) -> dict:
    return {"id": pid, "name": NAMES[pid]}


def _session(
    state: str | None = "open",
    *,
    mode: str = "auto_lock",
    touched=(),
    agent: str | None = CONVERSATION,
    status: str = "active",
) -> dict:
    """A session as the API returns it; auto_lock unless ``mode`` says otherwise."""
    locked = touched[0] if state in {"locked", "read_only"} and touched else None
    auto = mode == "auto_lock"
    return {
        "id": SID,
        "status": status,
        "mode": mode,
        "agent_session": agent,
        "client_name": "beakr mcp",
        "label": "claude-code",
        "folder": None,
        "remember_folder": False,
        "lock_state": state if auto else None,
        "write_project": _project(locked) if locked else None,
        "touched_projects": [_project(p) for p in touched],
        "read_only_at": _later(0) if state == "read_only" else None,
        "read_only_reason": (
            "Read " + " and ".join(NAMES[p] for p in touched) + "."
            if state == "read_only"
            else None
        ),
        "read_projects": [_project(p) for p in touched] if auto else [_project(A), _project(B)],
        "write_projects": ([_project(locked)] if locked else []) if auto else [_project(A)],
        "picker_url": f"https://app.test/sessions/{SID}",
        "run_label": f"session:{SID}",
    }


def _created(session: dict, token: str = "bk_st_first") -> dict:
    return {**session, "token": {"token": token, "token_type": "bearer", "expires_at": _later()}}


def _isolation(mode: str = "auto_lock", *, agent_sessions: bool = True) -> dict:
    return {
        "session_mode": mode,
        "auto_lock_counts_search_results": True,
        "agent_writes_require_session": False,
        "cross_project_guard": "allow",
        "available": {
            "server_agent_sessions": agent_sessions,
            "session_auto_lock": True,
            "agent_writes_require_session": True,
            "cross_project_guard": True,
        },
        "can_change": False,
    }


def _refusal(status: int, code: str, message: str, fix: str | None = None, **extra) -> dict:
    """The API's error envelope (``error``, and FastAPI's ``detail`` with its extras)."""
    return {
        "detail": {"code": code, "message": message, "fix": fix, **extra},
        "error": {"code": code, "message": message, "fix": fix},
    }


def _bearer(route) -> str:
    return route.calls.last.request.headers["authorization"]


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


def _text(result) -> str:
    return " ".join(result.output.split())


def _active(session: dict, *, agent: str | None = CONVERSATION) -> McpSession:
    held = McpSession(folder=None, agent=agent)
    held.state = TokenState(SID, "bk_st_first", _later())
    held.session = session
    scope_session.ACTIVE = held
    return held


# --- One session per agent conversation -----------------------------------------------


@respx.mock
def test_beakr_mcp_joins_the_conversations_session(conversation, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    created = respx.post(f"{API}/v1/sessions").respond(201, json=_created(_session()))

    held = asyncio.run(scope_session.start_for_mcp())

    assert held is not None and held.agent == CONVERSATION and held.token == "bk_st_first"
    assert _body(created)["agent_session"] == CONVERSATION
    # Joined by the body: the person's own credential carries no X-Beakr-Session, so
    # the API does not answer it from inside some conversation's session.
    assert "x-beakr-session" not in created.calls.last.request.headers


@respx.mock
def test_without_agent_sessions_beakr_mcp_starts_its_own_session_as_before(
    conversation, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    created = respx.post(f"{API}/v1/sessions").mock(
        side_effect=[
            httpx.Response(
                422,
                json=_refusal(422, "feature_not_enabled", "Agent sessions are not enabled here."),
            ),
            httpx.Response(201, json=_created(_session("open", mode="picker", agent=None))),
        ]
    )

    held = asyncio.run(scope_session.start_for_mcp())

    assert held is not None and held.token == "bk_st_first"
    assert "agent_session" in json.loads(created.calls[0].request.content)
    assert "agent_session" not in _body(created)


@respx.mock
def test_auto_lock_takes_no_remembered_projects_and_never_asks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1"})
    scope_session.remember_selection(tmp_path, [A, B], [A])
    created = respx.post(f"{API}/v1/sessions").mock(
        side_effect=[
            httpx.Response(
                422,
                json=_refusal(422, "session_mode_auto_lock", "This organization locks sessions."),
            ),
            httpx.Response(201, json=_created(_session(agent=None))),
        ]
    )

    held = asyncio.run(scope_session.start_for_mcp())

    assert "read_project_ids" in json.loads(created.calls[0].request.content)
    assert "read_project_ids" not in _body(created)
    # Active from the start: the gate lets every tool through, no picker, no form.
    assert held is not None and not held.pending
    assert asyncio.run(mcp_session.gate(SimpleNamespace(), "knowledge_base", None)) is None


@respx.mock
def test_picker_mode_still_answers_with_the_picker(conversation):
    pending = {**_session(None, mode="picker"), "status": "pending"}
    pending["read_projects"] = pending["write_projects"] = []
    _active(pending)
    respx.get(f"{API}/v1/sessions/current").respond(json=pending)
    server = SimpleNamespace(
        get_context=lambda: SimpleNamespace(
            session=SimpleNamespace(client_params=SimpleNamespace(capabilities=None))
        )
    )

    refusal = asyncio.run(mcp_session.gate(server, "knowledge_base", None))

    assert refusal is not None and refusal["error_code"] == "session_needs_selection"


@respx.mock
def test_a_cli_write_joins_the_session_beakr_mcp_holds(conversation, monkeypatch):
    """The MCP server and the command share one session id; the write uses its token."""
    monkeypatch.setattr(write_session, "ENABLED", True)
    respx.get(f"{API}/v1/organizations/{ORG}/project-isolation").respond(json=_isolation())
    joined = respx.post(f"{API}/v1/sessions").respond(
        201, json=_created(_session(), token="bk_st_cli")
    )
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    delete = respx.delete(f"{API}/v1/artifacts/{FILE}").respond(204)

    result = runner.invoke(app, ["files", "rm", FILE, "--yes"])

    assert result.exit_code == 0, result.output
    assert joined.call_count == 1
    assert _body(joined)["agent_session"] == CONVERSATION
    assert "read_project_ids" not in _body(joined)
    assert _bearer(delete) == "Bearer bk_st_cli"
    # No picker, no wait: auto_lock has nothing to choose.
    assert "picker" not in result.output.lower() and "Waiting" not in result.output
    assert scope_session.terminal_session_id(f"agent:{CONVERSATION}") == SID


@respx.mock
def test_cli_reads_in_an_auto_lock_conversation_carry_its_session(conversation):
    from beakr_cli.sdk import get_sdk

    settings = respx.get(f"{API}/v1/organizations/{ORG}/project-isolation").respond(
        json=_isolation()
    )
    joined = respx.post(f"{API}/v1/sessions").respond(
        201, json=_created(_session(), token="bk_st_cli")
    )
    me = respx.get(f"{API}/v1/me").respond(json={"user": {}})

    for _ in range(2):
        with get_sdk() as sdk:
            sdk.me.get()

    assert _bearer(me) == "Bearer bk_st_cli"
    assert _body(joined)["agent_session"] == CONVERSATION
    # Asked once per conversation, then kept with the terminal's session.
    assert settings.call_count == 1 and joined.call_count == 1
    assert _bearer(settings) == f"Bearer {PERSON_KEY}"


@respx.mock
def test_cli_reads_in_a_picker_conversation_stay_as_before(conversation):
    from beakr_cli.sdk import get_sdk

    settings = respx.get(f"{API}/v1/organizations/{ORG}/project-isolation").respond(
        json=_isolation("picker")
    )
    joined = respx.post(f"{API}/v1/sessions").respond(201, json=_created(_session()))
    me = respx.get(f"{API}/v1/me").respond(json={"user": {}})

    for _ in range(2):
        with get_sdk() as sdk:
            sdk.me.get()

    assert _bearer(me) == f"Bearer {PERSON_KEY}"
    assert settings.call_count == 1 and not joined.called


@respx.mock
def test_outside_an_agent_conversation_reads_never_ask():
    from beakr_cli.sdk import get_sdk

    settings = respx.get(f"{API}/v1/organizations/{ORG}/project-isolation").respond(
        json=_isolation()
    )
    me = respx.get(f"{API}/v1/me").respond(json={"user": {}})
    with get_sdk() as sdk:
        sdk.me.get()
    assert _bearer(me) == f"Bearer {PERSON_KEY}" and not settings.called


@respx.mock
def test_an_ended_conversation_session_is_said_so(conversation):
    from beakr_cli.sdk import get_sdk

    respx.get(f"{API}/v1/organizations/{ORG}/project-isolation").respond(json=_isolation())
    respx.post(f"{API}/v1/sessions").respond(
        409,
        json=_refusal(409, "session_ended", "This agent session has ended.", "Start a new one."),
    )
    with pytest.raises(SessionScopeError) as raised, get_sdk() as sdk:
        sdk.me.get()
    assert raised.value.code == "session_ended"
    assert CONVERSATION in raised.value.message
    assert "new agent conversation" in (raised.value.fix or "")


# --- Refusals ---------------------------------------------------------------------------


def _gate_write(monkeypatch, session: dict):
    """``beakr files rm`` through the gate, in a terminal (not an agent) that kept ``session``."""
    monkeypatch.setattr(write_session, "ENABLED", True)
    scope_session.save_state(TokenState(SID, "bk_st_kept", _later()))
    scope_session.remember_terminal(SID, mode=session.get("mode"))
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=session)


@respx.mock
def test_a_read_only_session_refuses_the_write_before_sending_it(monkeypatch):
    _gate_write(monkeypatch, _session("read_only", touched=(A, B), agent=None))
    respx.get(f"{API}/v1/projects/{B}").respond(json=_project(B))
    write = respx.post(f"{API}/v1/knowledge/wiki/proposals").respond(201, json={})

    result = runner.invoke(
        app, ["kb", "propose", "create", "--title", "Notes", "--content", "x", "--project", B]
    )

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert "is read-only: Read Pharma A and Pharma B. It cannot write Pharma B" in text
    assert f"beakr session end {SID}" in text and "run the command again" in text
    assert not write.called


@respx.mock
def test_the_apis_read_only_refusal_names_the_fix_for_an_agent_conversation(
    conversation, monkeypatch
):
    session = _session("locked", touched=(A,))
    monkeypatch.setattr(write_session, "ENABLED", True)
    scope_session.save_state(TokenState(SID, "bk_st_kept", _later()))
    scope_session.remember_terminal(SID, f"agent:{CONVERSATION}", mode="auto_lock")
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=session)
    respx.delete(f"{API}/v1/artifacts/{FILE}").respond(
        403,
        json=_refusal(
            403,
            "session_read_only",
            "This session read Pharma A and Pharma B, so it is read-only: writes are refused. "
            "Start a new session to write.",
            "Start a new agent conversation (or POST /v1/sessions) and work in one project.",
            session_id=SID,
            touched_projects=[_project(A), _project(B)],
        ),
    )

    result = runner.invoke(app, ["files", "rm", FILE, "--yes"])

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert "This session read Pharma A and Pharma B, so it is read-only" in text
    assert "Start a new agent conversation (a new Claude Code session" in text
    assert "POST /v1/sessions" not in text


@respx.mock
def test_a_locked_session_refuses_another_project_by_name(monkeypatch):
    _gate_write(monkeypatch, _session("locked", touched=(A,), agent=None))
    respx.get(f"{API}/v1/projects/{B}").respond(json=_project(B))
    write = respx.post(f"{API}/v1/knowledge/wiki/proposals").respond(201, json={})

    result = runner.invoke(
        app, ["kb", "propose", "create", "--title", "Notes", "--content", "x", "--project", B]
    )

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert (
        "is locked to Pharma A: it can write only to Pharma A, so it cannot write Pharma B" in text
    )
    # One lead-in only: the project is named in the message, the fix says what to do.
    assert "To fix: To write" not in text and "To write Pharma B:" not in text
    assert "nothing was written. To fix: End it with `beakr session end" in text
    assert not write.called


def test_an_open_or_locked_session_may_write_its_project():
    held = write_session.Held(_session("open"))
    assert held.may_write(A) and held.may_write(B)
    held = write_session.Held(_session("locked", touched=(A,)))
    assert held.may_write(A) and not held.may_write(B)
    held = write_session.Held(_session("read_only", touched=(A, B)))
    assert not held.may_write(A)
    # Picker mode: the write list, as before.
    held = write_session.Held(_session(None, mode="picker"))
    assert held.may_write(A) and not held.may_write(B)


@respx.mock
def test_a_write_outside_a_session_says_how_to_run_it_in_one():
    respx.delete(f"{API}/v1/artifacts/{FILE}").respond(
        403,
        json=_refusal(
            403,
            "session_required",
            "Your organization requires agents to write inside a session; this request writes "
            "with an API key and no session.",
            "Send X-Beakr-Session ...",
        ),
    )

    result = runner.invoke(app, ["files", "rm", FILE, "--yes"])

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert "requires agents to write inside a session" in text
    assert 'eval "$(beakr session start --env)"' in text
    # Not the other session_required (a browser session in the web app).
    assert "signed-in browser session" not in text


@respx.mock
def test_the_web_apps_session_required_keeps_its_own_hint():
    respx.delete(f"{API}/v1/artifacts/{FILE}").respond(
        403,
        json=_refusal(
            403, "session_required", "Deleting a file requires the Beakr web app, not a key."
        ),
    )

    result = runner.invoke(app, ["files", "rm", FILE, "--yes"])

    text = _text(result)
    assert "signed-in browser session" in text
    assert "beakr session start" not in text


@respx.mock
def test_the_guards_refusal_names_the_other_project_and_who_can_change_it():
    respx.delete(f"{API}/v1/artifacts/{FILE}").respond(
        403,
        json=_refusal(
            403,
            "cross_project_write_refused",
            "Pharma B does not accept content from other projects, and this write brings in "
            "content from Pharma A (cited).",
            "Remove what comes from Pharma A, or write it in Pharma A. An owner or admin of "
            "Pharma B can change this setting.",
        ),
    )

    result = runner.invoke(app, ["files", "rm", FILE, "--yes"])

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert "Pharma B does not accept content from other projects" in text
    assert "An owner or admin of Pharma B can change this setting" in text


_HOLD = {
    "reason": "Content from Pharma A (cited) and Pharma C (read in this session).",
    "projects": [{"id": A, "name": "Pharma A", "via": ["cited"]}],
    "approver": "owner_or_admin",
    "project": _project(B),
}


def _held_proposal() -> dict:
    return {
        "id": "prop-1",
        "proposal_type": "create",
        "status": "pending",
        "page_title": "Notes",
        "content_hash": "h1",
        "cross_project_hold": _HOLD,
        "needs_project_admin_to_accept": True,
    }


@respx.mock
def test_a_held_proposal_is_shown_with_who_must_approve_it():
    respx.get(f"{API}/v1/knowledge/wiki/proposals/prop-1").respond(json=_held_proposal())

    result = runner.invoke(app, ["kb", "proposals", "show", "prop-1"])

    assert result.exit_code == 0, result.output
    text = _text(result)
    assert "Held for approval by an owner or admin of Pharma B." in text
    assert "Pharma C (read in this session)" in text


@respx.mock
def test_knowledge_base_write_held_by_the_guard_is_a_normal_result():
    from beakr_cli.mcp_server import mcp

    respx.post(f"{API}/v1/knowledge/wiki/write").respond(
        json={
            "text": "Staged proposal prop-1 for Notes.",
            "outcome": {"status": "ok"},
            "proposal": _held_proposal(),
            "approval_state": "pending",
        }
    )

    result = asyncio.run(
        mcp.call_tool(
            "knowledge_base_write",
            {"action": "new", "arguments": {"title": "Notes"}, "scope": B},
        )
    )

    assert not result.isError
    text = result.content[0].text
    assert "Held for approval by an owner or admin of Pharma B." in text


# --- Seeing the session's state -------------------------------------------------------


def test_describe_shows_mode_lock_and_touched_projects():
    # An engine before project boundaries says auto_lock and picker: read as automatic
    # and picked.
    text = scope_session.describe(_session("read_only", touched=(A, B)))
    assert "Mode: automatic" in text
    assert "Read-only: Read Pharma A and Pharma B." in text
    assert "Touched: Pharma A, Pharma B" in text
    assert "new agent conversation" in text
    text = scope_session.describe(_session("locked", touched=(A,)))
    assert "Locked to Pharma A" in text
    assert "Open:" in scope_session.describe(_session("open"))
    # Picker mode reads as before, with its mode.
    picker = scope_session.describe(_session(None, mode="picker", touched=(A,)))
    assert "Reads: Pharma A, Pharma B" in picker and "Mode: picked" in picker


@respx.mock
def test_session_show_prints_the_lock_state():
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("locked", touched=(A,)))

    result = runner.invoke(app, ["session", "show", SID])

    assert result.exit_code == 0, result.output
    assert "Locked to Pharma A" in result.output and "Mode: automatic" in result.output


@respx.mock
def test_session_show_finds_the_agent_conversations_session(conversation):
    # Joined by the conversation's id; read with that session's own token.
    joined = respx.post(f"{API}/v1/sessions").respond(
        201, json=_created(_session("locked", touched=(A,)), token="bk_st_conv")
    )
    shown = respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("locked", touched=(A,)))

    result = runner.invoke(app, ["session", "show"])

    assert result.exit_code == 0, result.output
    assert f"Session {SID}" in result.output
    assert _body(joined)["agent_session"] == CONVERSATION
    assert _bearer(shown) == "Bearer bk_st_conv"


@respx.mock
def test_session_status_shows_the_state_without_changing_it():
    # An engine without the status route (its bare 404): the session's own description.
    _active(_session("read_only", touched=(A, B)))
    current = respx.get(f"{API}/v1/sessions/current").respond(
        json=_session("read_only", touched=(A, B))
    )
    respx.get(f"{API}/v1/sessions/current/status").respond(404, json={"detail": "Not Found"})

    text = asyncio.run(mcp_session.session_status())

    assert current.called
    assert "Read-only" in text and "Touched: Pharma A, Pharma B" in text


@respx.mock
def test_session_scope_does_not_narrow_an_auto_lock_session():
    _active(_session("open"))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session("open"))
    patched = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session("open"))

    refused = asyncio.run(mcp_session.session_scope(drop=[A]))

    assert refused["error_code"] == "session_not_picked"
    assert "automatic" in refused["message"]
    assert "session_status" in refused["fix"]
    assert not patched.called


@respx.mock
def test_list_projects_in_an_automatic_session_lists_every_project():
    from beakr_cli.mcp_server import mcp

    _active(_session("locked", touched=(A,)))
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1", "name": "Octant"})
    listed = respx.get(f"{API}/v1/projects").respond(
        json={"data": [_project(A), _project(B)], "has_more": False}
    )

    result = asyncio.run(mcp.call_tool("list_projects", {}))

    text = result.content[0].text
    assert listed.called and "Pharma A" in text and "Pharma B" in text
    # Where it can still write is session_status's to say, as on the hosted server.
    assert "This session:" not in text


# --- An agent conversation's session ended ----------------------------------------------


@respx.mock
def test_an_ended_conversation_session_is_not_replaced():
    from beakr_cli.mcp_errors import call_api

    held = _active(_session("locked", touched=(A,)))
    respx.get(f"{API}/v1/me").respond(
        401, json={"error": {"code": "invalid_token", "message": "ended", "fix": None}}
    )
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session(status="ended"))
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created(_session()))

    with pytest.raises(SessionScopeError) as raised:
        asyncio.run(call_api(lambda sdk: sdk.me.get()))

    assert raised.value.code == "session_ended"
    assert "new agent conversation" in (raised.value.fix or "")
    assert not started.called and held.state is None


def _ended(**extra) -> dict:
    return {
        **_session("locked", touched=(A,), status="ended"),
        "ended_at": "2026-11-11T21:30:00+00:00",
        **extra,
    }


_TRAIL = {
    "data": [
        {
            "id": "r-1",
            "session_id": SID,
            "at": "2026-11-11T21:10:00+00:00",
            "op": "search",
            "projects": [_project(A)],
            "items": [],
            "summary": '"assay results"',
            "redacted": False,
        }
    ],
    "next_cursor": None,
    "has_more": False,
}


@respx.mock
def test_session_status_still_answers_after_the_person_ended_the_session():
    """Reads and writes are refused; session_status says what it was and how to go on."""
    held = _active(_session("locked", touched=(A,)))
    respx.get(f"{API}/v1/sessions/current").respond(
        401, json={"error": {"code": "invalid_token", "message": "ended", "fix": None}}
    )
    record = respx.get(f"{API}/v1/sessions/{SID}").respond(json=_ended())
    trail = respx.get(f"{API}/v1/sessions/{SID}/trail").respond(json=_TRAIL)

    text = asyncio.run(mcp_session.session_status(trail=True))

    assert isinstance(text, str), text
    assert f"Session {SID} (ended)" in text
    assert "Ended at 2026-11-11 21:30:00." in text
    assert "start a new Claude Code conversation" in text
    assert "Touched: Pharma A" in text
    assert '"assay results"' in text
    # Read as the person: the session's own token no longer works.
    assert record.calls.last.request.headers["authorization"] == f"Bearer {PERSON_KEY}"
    assert trail.calls.last.request.headers["authorization"] == f"Bearer {PERSON_KEY}"
    assert held.state is None
    # Asked again, it answers the same way without the token.
    again = asyncio.run(mcp_session.session_status())
    assert isinstance(again, str) and "(ended)" in again


@respx.mock
def test_session_status_finds_an_ended_session_the_server_never_held(conversation):
    """beakr mcp started after the conversation's session ended (409 on start)."""
    held = McpSession(folder=None, agent=CONVERSATION)
    scope_session.ACTIVE = held
    respx.post(f"{API}/v1/sessions").respond(
        409, json=_refusal(409, "session_ended", "This agent session has ended.")
    )
    respx.get(f"{API}/v1/sessions").respond(
        json={"data": [_session(agent="other"), _ended()], "has_more": False}
    )

    text = asyncio.run(mcp_session.session_status())

    assert isinstance(text, str), text
    assert f"Session {SID} (ended)" in text and "new Claude Code conversation" in text


@respx.mock
def test_session_show_shows_an_ended_conversation_session(conversation):
    respx.post(f"{API}/v1/sessions").respond(
        409, json=_refusal(409, "session_ended", "This agent session has ended.")
    )
    respx.get(f"{API}/v1/sessions").respond(json={"data": [_ended()], "has_more": False})
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_ended())

    result = runner.invoke(app, ["session", "show"])

    assert result.exit_code == 0, result.output
    text = _text(result)
    assert f"Session {SID} (ended)" in text
    assert "start a new Claude Code conversation" in text


@respx.mock
def test_ending_a_conversation_session_says_it_is_final(conversation):
    respx.post(f"{API}/v1/sessions").respond(201, json=_created(_session()))
    respx.post(f"{API}/v1/sessions/{SID}/end").respond(json=_session(status="ended"))

    result = runner.invoke(app, ["session", "end", SID])

    assert result.exit_code == 0, result.output
    assert "never started again" in _text(result)


@respx.mock
def test_the_engines_own_held_line_is_not_said_twice():
    from beakr_cli.mcp_server import mcp

    respx.post(f"{API}/v1/knowledge/wiki/write").respond(
        json={
            "text": "Staged proposal prop-1. Held for an owner or admin of Pharma B to accept: "
            "content from Pharma A (cited).",
            "outcome": {"status": "ok"},
            "proposal": _held_proposal(),
        }
    )

    result = asyncio.run(
        mcp.call_tool(
            "knowledge_base_write",
            {"action": "new", "arguments": {"title": "Notes"}, "scope": B},
        )
    )

    text = result.content[0].text
    assert not result.isError
    assert text.lower().count("held for") == 1


def test_the_hold_names_what_was_cited_and_whether_the_reader_may_accept():
    from beakr_cli.proposal_view import held_line

    hold = {
        **_HOLD,
        "projects": [
            {
                "id": A,
                "name": "Pharma A",
                "via": ["cited"],
                "cited": [{"title": "assay.pdf", "source_type": "artifact"}],
            }
        ],
        "can_accept": True,
    }
    line = held_line({"cross_project_hold": hold})
    assert "Cited: assay.pdf (Pharma A)." in line
    assert "You may accept it" in line
    line = held_line({"cross_project_hold": {**hold, "can_accept": False}})
    assert "Only an owner or admin of Pharma B can accept it" in line


@respx.mock
def test_a_card_row_write_held_by_the_guard_says_so():
    """add-fact's reply carries the hold in its proposal ref (the write response)."""
    respx.post(f"{API}/v1/knowledge/wiki/write").respond(
        json={
            "text": "Staged proposal prop-1: 1 Facts row on Compound X.",
            "outcome": {"status": "ok"},
            "proposal": {"id": "prop-1", "content_hash": "h1", "cross_project_hold": _HOLD},
        }
    )

    result = runner.invoke(
        app,
        [
            "kb", "add-fact", "Compound X", "--attribute", "ic50", "--value", "12 nM",
            "--evidence", "assay.pdf", "--project", B, "--rationale", "r",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "Held for approval by an owner or admin of Pharma B." in _text(result)


# --- One conversation, one session row (rc.8 QA) ----------------------------------------


class _ConversationApi:
    """An API with agent sessions on, keeping its session rows as the engine keys them.

    A ``POST /v1/sessions`` with ``agent_session`` joins (or starts) that
    conversation's row; one without starts an unkeyed row; any request a person's
    key sends with ``X-Beakr-Session`` (and no session token) is joined to that id's
    row, started on first sight. rc.8 QA saw three rows for one Claude Code
    conversation: the conversation's, an unkeyed ``beakr mcp`` row, and an
    ``sdk-python-<random>`` decoy.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.unkeyed: list[dict] = []

    def _row(self, key: str) -> dict:
        if key not in self.rows:
            self.rows[key] = {**_session(agent=key), "id": f"row-{len(self.rows)}"}
        return self.rows[key]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("authorization", "")
        header = request.headers.get("x-beakr-session")
        if header and not bearer.startswith("Bearer bk_st_"):
            self._row(header)
        path = request.url.path
        if request.method == "POST" and path == "/v1/sessions":
            body = json.loads(request.content)
            if body.get("agent_session"):
                row = self._row(body["agent_session"])
            else:
                row = {**_session(agent=None), "id": f"unkeyed-{len(self.unkeyed)}"}
                self.unkeyed.append(row)
            return httpx.Response(201, json=_created(row, token=f"bk_st_{row['id']}"))
        if path == f"/v1/organizations/{ORG}/project-isolation":
            return httpx.Response(200, json=_isolation())
        if path == "/v1/sessions/current":
            return httpx.Response(200, json=_session())
        for row in [*self.rows.values(), *self.unkeyed]:
            if path == f"/v1/sessions/{row['id']}":
                return httpx.Response(200, json=row)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"user": {}})


@respx.mock
def test_one_conversation_makes_one_session_row(conversation, tmp_path, monkeypatch):
    """beakr mcp, a CLI read and a write in one Claude Code conversation: one row."""
    from beakr_sdk import _base_client

    from beakr_cli.mcp_errors import call_api
    from beakr_cli.sdk import get_sdk

    # The SDK as built since its per-client default: a made-up id unless told one.
    original = _base_client.BaseClient.__init__

    def init(self, *args, session=None, **kwargs):
        made_up = "sdk-python-0123456789abcdef0123456789abcdef"
        original(self, *args, session=made_up if session is None else session, **kwargs)

    monkeypatch.setattr(_base_client.BaseClient, "__init__", init)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(write_session, "ENABLED", True)
    api = _ConversationApi()
    respx.route(host="api.test").mock(side_effect=api)

    held = asyncio.run(scope_session.start_for_mcp())
    asyncio.run(call_api(lambda sdk: sdk.me.get()))
    with get_sdk() as sdk:
        sdk.me.get()
    deleted = runner.invoke(app, ["files", "rm", FILE, "--yes"])

    assert deleted.exit_code == 0, deleted.output
    assert held is not None
    assert list(api.rows) == [CONVERSATION], api.rows
    assert api.unkeyed == []
    # The MCP server, the CLI's reads and its write all hold the conversation's row.
    assert held.id == api.rows[CONVERSATION]["id"]
    assert scope_session.terminal_session_id(f"agent:{CONVERSATION}") == held.id


@respx.mock
def test_without_hooks_one_conversation_still_makes_one_row(conversation, tmp_path, monkeypatch):
    """A self-hosted build runs no hooks; beakr mcp alone keys the conversation's row."""
    from beakr_cli import updates
    from beakr_cli.mcp_errors import call_api

    monkeypatch.setattr(updates, "__version__", "0.5.0+selfhosted.g0123456789ab")
    monkeypatch.chdir(tmp_path)
    api = _ConversationApi()
    respx.route(host="api.test").mock(side_effect=api)

    held = asyncio.run(scope_session.start_for_mcp())
    asyncio.run(call_api(lambda sdk: sdk.me.get()))
    # What a Beakr Claude Code plugin's hooks would run: nothing reaches the API.
    for args in (
        ["context", "--format", "claude_md", "--project", "auto", "--hook"],
        ["compound", "--hook", "--client", "claude-code", "--background"],
    ):
        assert runner.invoke(app, args, input="{}").exit_code == 0

    assert held is not None
    assert list(api.rows) == [CONVERSATION] and api.unkeyed == []
    assert held.id == api.rows[CONVERSATION]["id"]
    assert not any(
        call.request.headers.get("x-beakr-client", "").startswith("claude-code-hook")
        for call in respx.calls
    )


@respx.mock
def test_the_apis_locked_refusal_has_one_lead_in(conversation, monkeypatch):
    """rc.9 QA: "nothing was written. To fix: To write Pharma B: End it ..."."""
    monkeypatch.setattr(write_session, "ENABLED", True)
    scope_session.save_state(TokenState(SID, "bk_st_kept", _later()))
    scope_session.remember_terminal(SID, f"agent:{CONVERSATION}", mode="auto_lock")
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("open"))
    respx.delete(f"{API}/v1/artifacts/{FILE}").respond(
        403,
        json=_refusal(
            403,
            "session_locked_to_project",
            "This session reads and writes Pharma A, so it cannot write Pharma B.",
            "Start a new session to write Pharma B.",
            session_id=SID,
            refused_projects=[_project(B)],
        ),
    )

    result = runner.invoke(app, ["files", "rm", FILE, "--yes"])

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert "so it cannot write Pharma B. To fix: Start a new agent conversation" in text
    assert "To write" not in text


def test_a_held_proposal_in_a_person_review_project_says_both():
    """needs_person_to_accept is the project's policy alone, so both lines are true."""
    from beakr_cli.mcp_server import _format_proposal

    text = _format_proposal({**_held_proposal(), "needs_person_to_accept": True})
    assert "Held for approval by an owner or admin of Pharma B." in text
    assert "this project requires a person to accept" in text
    # Held in a project without that policy: the held line alone.
    alone = _format_proposal({**_held_proposal(), "needs_person_to_accept": False})
    assert "this project requires a person to accept" not in alone
