"""Project boundaries (beakr-engine ``max/project-boundaries``): session modes, the
valid-options check, session status, boundaries in the project list, and lock notices.

What each piece promises, one test or more each:

- ``beakr session start`` starts a picked session (``--read A,B --write A``, checked
  first with ``POST /v1/sessions/options``), a read-everything one
  (``--read-everything``) or an automatic one (``--automatic``, refused where every
  session must pick), and prints the session's one line; a choice that breaks a
  boundary is refused with each reason, before anything starts;
- inside an agent conversation it declares that conversation's own session, as the
  ``start_session`` tool does, and the conversation's commands then run in it;
- ``beakr session status`` and the ``session_status`` tool say where the session can
  still write, and why not elsewhere;
- ``beakr projects ls`` and ``list_projects`` show each project's boundary in plain words;
- ``beakr mcp``'s instructions explain the two rules and the three modes, and its
  ``start_session`` tool declares the session;
- a lock notice (``X-Beakr-Session-Notice``) reaches the agent verbatim: the tool
  result's first line, or stderr at a terminal.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import mcp_session, scope_session, session_notice
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import McpSession, TokenState

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
CHEM = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
NAMES = {A: "Partner A", CHEM: "Chemistry"}
PERSON_KEY = "bk_live_person_key_for_tests"
ORG = "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"
CONVERSATION = "conv-123"
ISOLATION = f"{API}/v1/organizations/{ORG}/project-isolation"
OPTIONS = f"{API}/v1/sessions/options"

SILOED = (
    "Partner A: siloed. Touching it locks this session's writes to it. Writes that bring in "
    "other projects' content wait for its owners to approve."
)
INTERNAL = "Chemistry: internal. No boundary: sessions read and write it freely."
LOCKED_NOTICE = "This session is now locked: it can write only to Partner A."

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)
    monkeypatch.delenv(scope_session.MCP_SESSIONS_ENV, raising=False)
    monkeypatch.setattr("beakr_cli.project_binding.credential_org_id", lambda: ORG)
    scope_session.ACTIVE = None
    yield
    scope_session.ACTIVE = None


@pytest.fixture
def conversation(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CONVERSATION)
    return CONVERSATION


def _later(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _project(pid: str, summary: str | None = None) -> dict:
    project = {"id": pid, "name": NAMES[pid], "project_type": "standard", "status": "active"}
    if summary is not None:
        siloed = pid == A
        project["boundary"] = {
            "content_out": "locked" if siloed else "allowed",
            "content_in": "owner_approves" if siloed else "allowed",
            "preset": "siloed" if siloed else "internal",
            "stored": None,
            "raised_by_minimum": False,
            "summary": summary,
            "can_change": True,
        }
    return project


def _ref(pid: str) -> dict:
    return {"id": pid, "name": NAMES[pid]}


def _session(mode: str = "picked", **extra) -> dict:
    picked = mode == "picked"
    return {
        "id": SID,
        "status": "active",
        "mode": mode,
        "agent_session": None,
        "client_name": "beakr-cli",
        "label": "beakr shell",
        "folder": None,
        "remember_folder": False,
        "lock_state": "read_only" if mode == "read_everything" else "open",
        "write_project": None,
        "touched_projects": [],
        "read_touched_projects": [],
        "read_only_reason": "Reads everything, writes nothing."
        if mode == "read_everything"
        else None,
        "read_projects": [_ref(A), _ref(CHEM)] if picked else [],
        "write_projects": [_ref(A)] if picked else [],
        "picker_url": f"https://app.test/sessions/{SID}",
        "run_label": f"session:{SID}",
        "summary": {
            "picked": "Picked session: reads Partner A and Chemistry; writes Partner A.",
            "read_everything": (
                "Read-everything session: reads every project you can read, Partner A "
                "included; writes nothing."
            ),
            "automatic": (
                "Automatic session: reads every project you can read; where it can write "
                "follows what it touches."
            ),
        }[mode],
        "notes": [],
        **extra,
    }


def _created(session: dict, token: str = "bk_st_first") -> dict:
    return {**session, "token": {"token": token, "token_type": "bearer", "expires_at": _later()}}


def _valid() -> dict:
    return {"valid": True, "reasons": [], "notes": [], "projects": []}


def _invalid() -> dict:
    return {
        "valid": False,
        "reasons": [
            {
                "code": "locked_read_limits_writes",
                "message": "Reading Partner A locks this session's writes to Partner A, so it "
                "can't also write to Chemistry.",
                "project_ids": [A, CHEM],
            }
        ],
        "notes": [],
        "projects": [],
    }


def _status(**extra) -> dict:
    return {
        "session_id": SID,
        "status": "active",
        "mode": "automatic",
        "lock_state": "locked",
        "locked_to": _ref(A),
        "read_projects": [_ref(A)],
        "touched_projects": [_ref(A)],
        "reads_everything": True,
        "readable_projects": [],
        "write": {
            "anywhere": False,
            "projects": [{**_ref(A), "outcome": "write", "reason": None}],
            "refused": [
                {
                    **_ref(CHEM),
                    "code": "session_locked_to_project",
                    "reason": "This session read Partner A, so it can write only to Partner A.",
                }
            ],
        },
        "summary": "Automatic session. It read Partner A, which keeps its content in, so it can "
        "write only to Partner A. Start a new session to work in another project.",
        "events": [
            {
                "at": "2026-11-13T21:04:11Z",
                "op": "boundary:locked",
                "project_ids": [A],
                "summary": "Locked to Partner A by knowledge_base sections (scope Partner A).",
            }
        ],
        **extra,
    }


def _refusal(status: int, code: str, message: str, fix: str | None = None, **extra) -> dict:
    return {
        "detail": {"code": code, "message": message, "fix": fix, **extra},
        "error": {"code": code, "message": message, "fix": fix, "details": extra},
    }


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


def _bearer(route) -> str:
    return route.calls.last.request.headers["authorization"]


def _text(result) -> str:
    return " ".join(result.output.split())


def _active(session: dict, *, agent: str | None = CONVERSATION) -> McpSession:
    held = McpSession(folder=None, agent=agent)
    held.state = TokenState(SID, "bk_st_first", _later())
    held.session = session
    scope_session.ACTIVE = held
    return held


# --- beakr session start: picked, read everything, automatic --------------------------


@respx.mock
def test_start_picked_checks_the_options_then_prints_the_one_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    options = respx.post(OPTIONS).respond(json=_valid())
    created = respx.post(f"{API}/v1/sessions").respond(201, json=_created(_session()))

    result = runner.invoke(app, ["session", "start", "--read", f"{A},{CHEM}", "--write", A])

    assert result.exit_code == 0, result.output
    assert _body(options) == {"read_project_ids": [A, CHEM], "write_project_ids": [A]}
    assert _bearer(options) == f"Bearer {PERSON_KEY}"
    body = _body(created)
    assert body["read_project_ids"] == [A, CHEM] and body["write_project_ids"] == [A]
    assert "read_everything" not in body
    text = _text(result)
    assert "Picked session: reads Partner A and Chemistry; writes Partner A." in text
    assert "Reads: Partner A, Chemistry" in text and "Writes: Partner A" in text


@respx.mock
def test_start_refuses_a_choice_that_breaks_a_boundary_before_starting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.post(OPTIONS).respond(json=_invalid())
    created = respx.post(f"{API}/v1/sessions")

    result = runner.invoke(
        app, ["session", "start", "--read", A, "--write", f"{A},{CHEM}", "--json"]
    )

    assert result.exit_code != 0, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "session_selection_invalid"
    assert error["message"] == (
        "Reading Partner A locks this session's writes to Partner A, so it can't also write to "
        "Chemistry."
    )
    assert "Change --read or --write" in error["fix"]
    assert not created.called


@respx.mock
def test_the_apis_selection_refusal_says_every_reason_on_its_own_line(tmp_path, monkeypatch):
    # An engine that answers the start itself (the options route left out).
    monkeypatch.chdir(tmp_path)
    respx.post(OPTIONS).respond(404, json={"detail": "Not Found"})
    reasons = [
        {"code": "locked_read_limits_writes", "message": "Reading Partner A locks writes."},
        {"code": "blocked_write_limits_reads", "message": "Chemistry takes no content."},
    ]
    respx.post(f"{API}/v1/sessions").respond(
        422,
        json=_refusal(
            422,
            "session_selection_invalid",
            "Reading Partner A locks writes.",
            "Change the read or write list, or check the options with POST /v1/sessions/options.",
            reasons=reasons,
        ),
    )

    result = runner.invoke(app, ["session", "start", "--read", A, "--write", CHEM])

    assert result.exit_code != 0, result.output
    assert "Reading Partner A locks writes.\nChemistry takes no content." in result.output.replace(
        "Error: ", ""
    )


@respx.mock
def test_start_read_everything_sends_no_lists_and_checks_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    options = respx.post(OPTIONS)
    created = respx.post(f"{API}/v1/sessions").respond(
        201, json=_created(_session("read_everything"))
    )

    result = runner.invoke(app, ["session", "start", "--read-everything"])

    assert result.exit_code == 0, result.output
    body = _body(created)
    assert body["read_everything"] is True
    assert "read_project_ids" not in body and "write_project_ids" not in body
    assert not options.called
    assert "Read-everything session" in _text(result)
    assert "writes nothing" in _text(result)


@pytest.mark.parametrize(
    "argv",
    [
        ["--read-everything", "--read", A],
        ["--automatic", "--write", A],
        ["--automatic", "--read-everything"],
        ["--pending", "--read-everything"],
    ],
)
def test_start_takes_one_mode(argv):
    result = runner.invoke(app, ["session", "start", *argv])

    assert result.exit_code == 2, result.output
    assert "combined" in result.output and "a session is picked" in _text(result)


@respx.mock
def test_start_automatic_starts_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.get(ISOLATION).respond(json={"session_mode": "automatic"})
    created = respx.post(f"{API}/v1/sessions").respond(201, json=_created(_session("automatic")))

    result = runner.invoke(app, ["session", "start", "--automatic"])

    assert result.exit_code == 0, result.output
    body = _body(created)
    assert not {"read_project_ids", "write_project_ids", "read_everything"} & set(body)
    assert "Automatic session" in _text(result)
    assert "Open: it writes where project boundaries allow" in _text(result)


@respx.mock
def test_start_automatic_is_refused_where_every_session_must_pick(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.get(ISOLATION).respond(json={"session_mode": "must_pick"})
    created = respx.post(f"{API}/v1/sessions")

    result = runner.invoke(app, ["session", "start", "--automatic"])

    assert result.exit_code != 0, result.output
    assert "must_pick" in _text(result) and "--read-everything" in _text(result)
    assert not created.called


@respx.mock
def test_start_env_says_the_one_line_on_stderr(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.post(OPTIONS).respond(json=_valid())
    respx.post(f"{API}/v1/sessions").respond(201, json=_created(_session()))

    result = runner.invoke(app, ["session", "start", "--read", A, "--write", A, "--env"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"export {scope_session.SESSION_ENV}={SID}"
    assert "Picked session: reads Partner A and Chemistry" in result.stderr


@respx.mock
def test_edit_read_everything_sends_the_mode():
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    patched = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session("read_everything"))

    result = runner.invoke(app, ["session", "edit", SID, "--read-everything"])

    assert result.exit_code == 0, result.output
    assert _body(patched) == {"read_everything": True}


@respx.mock
def test_edit_automatic_sends_the_mode():
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    patched = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session("automatic"))

    result = runner.invoke(app, ["session", "edit", SID, "--automatic"])

    assert result.exit_code == 0, result.output
    assert _body(patched) == {"mode": "automatic"}


@respx.mock
def test_edit_checks_the_options_first():
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    respx.post(OPTIONS).respond(json=_invalid())
    patched = respx.patch(f"{API}/v1/sessions/{SID}")

    result = runner.invoke(app, ["session", "edit", SID, "--read", A, "--write", CHEM])

    assert result.exit_code != 0, result.output
    assert "can't also write to Chemistry" in _text(result)
    assert not patched.called


# --- Inside an agent conversation: declaring its own session -----------------------------


@pytest.mark.real_conversation_sessions
@respx.mock
def test_start_in_a_conversation_declares_its_own_session(conversation, tmp_path, monkeypatch):
    from beakr_cli.sdk import get_sdk

    monkeypatch.chdir(tmp_path)
    options = respx.post(OPTIONS).respond(json=_valid())
    declared = respx.post(f"{API}/v1/sessions").respond(
        201, json=_created({**_session(), "agent_session": CONVERSATION}, token="bk_st_conv")
    )
    me = respx.get(f"{API}/v1/me").respond(json={"user": {}})

    result = runner.invoke(app, ["session", "start", "--read", f"{A},{CHEM}", "--write", A])

    assert result.exit_code == 0, result.output
    assert options.called
    body = _body(declared)
    assert body["agent_session"] == CONVERSATION
    assert body["read_project_ids"] == [A, CHEM] and body["write_project_ids"] == [A]
    # The person's own credential, joined to no session: the API applies it only
    # while the session has touched nothing.
    assert _bearer(declared) == f"Bearer {PERSON_KEY}"
    assert "x-beakr-session" not in declared.calls.last.request.headers
    assert (
        "Session started. Picked session: reads Partner A and Chemistry; writes Partner A."
        in _text(result)
    )
    # The conversation's commands now run in it (a picked session, declared).
    with get_sdk() as sdk:
        sdk.me.get()
    assert _bearer(me) == "Bearer bk_st_conv"


@pytest.mark.real_conversation_sessions
@respx.mock
def test_start_in_a_conversation_that_already_worked_says_so(conversation, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.post(f"{API}/v1/sessions").respond(
        409,
        json=_refusal(
            409,
            "session_already_started",
            "This session has already worked in Partner A, so its projects can't be chosen now.",
            "Start a new session (a new agent conversation) to choose its projects.",
        ),
    )

    result = runner.invoke(app, ["session", "start", "--read-everything"])

    assert result.exit_code != 0, result.output
    assert "has already worked in Partner A" in _text(result)
    assert "a new agent conversation" in _text(result)


@pytest.mark.parametrize("flag", ["--env", "--pending"])
@respx.mock
def test_start_in_a_conversation_refuses_shell_options(conversation, flag):
    api = respx.route(host="api.test").respond(200, json={})

    result = runner.invoke(app, ["session", "start", flag])

    assert result.exit_code == 3, result.output
    assert "declares that conversation's own session" in _text(result)
    assert not api.called


@pytest.mark.real_conversation_sessions
@respx.mock
def test_an_automatic_organizations_conversation_runs_in_its_session(conversation):
    from beakr_cli.sdk import get_sdk

    respx.get(ISOLATION).respond(
        json={
            "session_mode": "automatic",
            "available": {"server_agent_sessions": True, "project_boundaries": True},
        }
    )
    joined = respx.post(f"{API}/v1/sessions").respond(
        201, json=_created(_session("automatic", agent_session=CONVERSATION), token="bk_st_cli")
    )
    me = respx.get(f"{API}/v1/me").respond(json={"user": {}})

    with get_sdk() as sdk:
        sdk.me.get()

    assert _bearer(me) == "Bearer bk_st_cli"
    assert _body(joined)["agent_session"] == CONVERSATION


@pytest.mark.real_conversation_sessions
@respx.mock
def test_a_must_pick_organizations_conversation_reads_as_before(conversation):
    from beakr_cli.sdk import get_sdk

    respx.get(ISOLATION).respond(
        json={
            "session_mode": "must_pick",
            "available": {"server_agent_sessions": True, "project_boundaries": True},
        }
    )
    joined = respx.post(f"{API}/v1/sessions")
    me = respx.get(f"{API}/v1/me").respond(json={"user": {}})

    with get_sdk() as sdk:
        sdk.me.get()

    assert _bearer(me) == f"Bearer {PERSON_KEY}" and not joined.called


# --- Session status -------------------------------------------------------------------


@respx.mock
def test_session_status_says_where_it_can_still_write(monkeypatch):
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    route = respx.get(f"{API}/v1/sessions/{SID}/status").respond(json=_status())

    result = runner.invoke(app, ["session", "status"])

    assert result.exit_code == 0, result.output
    assert _bearer(route) == f"Bearer {PERSON_KEY}"
    lines = result.output.splitlines()
    assert lines[0].startswith("Automatic session. It read Partner A")
    assert lines[1:] == [
        "Read: Partner A.",
        "Can write: Partner A.",
        "Can't write Chemistry: This session read Partner A, so it can write only to Partner A.",
        "Recent:",
        "- Locked to Partner A by knowledge_base sections (scope Partner A).",
    ]


def test_status_lines_for_an_open_automatic_session():
    lines = scope_session.status_lines(
        _status(
            lock_state="open",
            write={
                "anywhere": True,
                "projects": [],
                "refused": [
                    {
                        **_ref(CHEM),
                        "code": "cross_project_write_refused",
                        "reason": "Chemistry takes no content from other projects.",
                    }
                ],
            },
            events=[],
        )
    )

    assert "Can write: any project you can read, except:" in lines
    assert "Can't write Chemistry: Chemistry takes no content from other projects." in lines


def test_status_lines_say_a_held_project():
    lines = scope_session.status_lines(
        _status(
            reads_everything=False,
            mode="picked",
            readable_projects=[_ref(A), _ref(CHEM)],
            write={
                "anywhere": False,
                "projects": [{**_ref(A), "outcome": "held", "reason": None}],
                "refused": [],
            },
        )
    )

    assert "Can write: Partner A (held for its owners)." in lines


def test_status_lines_for_a_read_everything_session():
    lines = scope_session.status_lines(
        _status(mode="read_everything", write={"anywhere": False, "projects": [], "refused": []})
    )

    assert "Can write: nothing." in lines


def test_session_status_outside_a_session_says_no_rules_apply():
    result = runner.invoke(app, ["session", "status"])

    assert result.exit_code == 0, result.output
    assert scope_session.NOT_IN_SESSION in _text(result)


@pytest.mark.real_conversation_sessions
@respx.mock
def test_session_status_in_a_conversation_asks_with_its_own_token(conversation, tmp_path):
    scope_session.save_state(TokenState(SID, "bk_st_conv", _later()))
    scope_session.remember_terminal(SID, f"agent:{CONVERSATION}", mode="automatic")
    route = respx.get(f"{API}/v1/sessions/current/status").respond(json=_status())

    result = runner.invoke(app, ["session", "status"])

    assert result.exit_code == 0, result.output
    assert _bearer(route) == "Bearer bk_st_conv"


# --- The project list -------------------------------------------------------------------


@respx.mock
def test_projects_ls_shows_each_boundary_in_plain_words():
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [_project(A, SILOED), _project(CHEM, INTERNAL)], "has_more": False}
    )

    piped = runner.invoke(app, ["projects", "ls"])

    assert piped.exit_code == 0, piped.output
    first, second = piped.stdout.splitlines()[:2]
    assert first.startswith(A) and first.endswith(f"\tboundary={SILOED}")
    assert second.endswith(f"\tboundary={INTERNAL}")


@respx.mock
def test_projects_ls_without_boundaries_is_as_before():
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{**_project(A), "boundary": None}], "has_more": False}
    )

    piped = runner.invoke(app, ["projects", "ls"])

    assert piped.exit_code == 0, piped.output
    assert "boundary=" not in piped.stdout


# --- projects boundary --------------------------------------------------------------------


@respx.mock
def test_boundary_show_says_it_in_plain_words():
    respx.get(f"{API}/v1/projects/{A}").respond(json=_project(A, SILOED))

    result = runner.invoke(app, ["projects", "boundary", "show", A])

    assert result.exit_code == 0, result.output
    assert "content_out\tlocked" in result.stdout and "preset\tsiloed" in result.stdout
    assert f"summary\t{SILOED}" in result.stdout


@respx.mock
def test_boundary_set_sends_a_preset_or_the_values():
    respx.get(f"{API}/v1/projects/{CHEM}").respond(json=_project(CHEM, INTERNAL))
    patched = respx.patch(f"{API}/v1/projects/{CHEM}").respond(json=_project(CHEM, INTERNAL))

    preset = runner.invoke(app, ["projects", "boundary", "set", CHEM, "--preset", "siloed"])
    assert preset.exit_code == 0, preset.output
    assert _body(patched) == {"boundary_preset": "siloed"}

    values = runner.invoke(app, ["projects", "boundary", "set", CHEM, "--in", "blocked"])
    assert values.exit_code == 0, values.output
    assert _body(patched) == {"boundary": {"content_in": "blocked"}}
    assert _bearer(patched) == f"Bearer {PERSON_KEY}"
    assert "x-beakr-session" not in patched.calls.last.request.headers


@respx.mock
def test_boundary_set_loosening_asks_first():
    respx.get(f"{API}/v1/projects/{A}").respond(json=_project(A, SILOED))
    patched = respx.patch(f"{API}/v1/projects/{A}").respond(json=_project(A, SILOED))

    refused = runner.invoke(app, ["projects", "boundary", "set", A, "--out", "allowed"])

    assert refused.exit_code == 2, refused.output
    assert "Partner A's content out locked -> allowed" in _text(refused)
    assert not patched.called


@respx.mock
def test_boundary_set_says_the_apis_minimum_refusal():
    respx.get(f"{API}/v1/projects/{A}").respond(json=_project(A, SILOED))
    message = (
        "Your organization's minimum boundary is {content_out: locked}, so Partner A can't be "
        "set below it."
    )
    respx.patch(f"{API}/v1/projects/{A}").respond(
        422,
        json=_refusal(422, "boundary_below_minimum", message, minimum={"content_out": "locked"}),
    )

    result = runner.invoke(app, ["projects", "boundary", "set", A, "--out", "allowed", "--yes"])

    assert result.exit_code != 0, result.output
    assert message in _text(result)


@respx.mock
def test_boundary_set_where_boundaries_are_off_exits_4():
    respx.get(f"{API}/v1/projects/{A}").respond(json={**_project(A), "boundary": None})
    patched = respx.patch(f"{API}/v1/projects/{A}")

    result = runner.invoke(app, ["projects", "boundary", "set", A, "--preset", "siloed"])

    assert result.exit_code == 4, result.output
    assert "Project boundaries (cross_project_guard)" in _text(result)
    assert not patched.called


@respx.mock
def test_projects_guard_is_deprecated_but_works():
    guard = {
        "project_id": A,
        "project_name": "Partner A",
        "override": None,
        "org_default": "hold",
        "effective": "hold",
        "available": True,
        "can_change": True,
    }
    respx.get(f"{API}/v1/projects/{A}/cross-project-guard").respond(json=guard)

    result = runner.invoke(app, ["projects", "guard", "show", A])

    assert result.exit_code == 0, result.output
    assert "Deprecated: `beakr projects guard` is now a project's boundary" in _text(result)
    assert "effective\thold" in result.stdout


# --- beakr mcp: instructions, start_session, session_status, list_projects ---------------


def test_instructions_explain_the_two_rules_and_three_modes():
    from beakr_cli.mcp_server import BOUNDARIES_INSTRUCTIONS, instructions

    text = instructions()
    assert BOUNDARIES_INSTRUCTIONS in text
    for words in (
        "Content out: once the session reads or writes a project",
        "Content in: a project that takes no content from other projects",
        "automatic (the default; the server tracks what it touches)",
        "picked (call start_session with read and write before anything else)",
        "read everything (start_session read_everything=true",
        "session_status says where this session can still write and why.",
    ):
        assert words in text


def test_start_session_and_session_status_are_listed_with_a_session():
    from mcp.server.fastmcp import FastMCP

    from beakr_cli.mcp_server import mcp

    names = {tool.name for tool in asyncio.run(FastMCP.list_tools(mcp))}
    assert {"start_session", "session_status"} <= names
    assert "start_session" in mcp_session.UNGATED_TOOLS


@respx.mock
def test_start_session_declares_the_conversations_session_by_name():
    held = _active({**_session("automatic"), "agent_session": CONVERSATION})
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [_project(A), _project(CHEM)], "has_more": False}
    )
    declared = respx.post(f"{API}/v1/sessions").respond(
        201, json=_created({**_session(), "agent_session": CONVERSATION}, token="bk_st_new")
    )

    text = asyncio.run(mcp_session.start_session(read=["Chemistry"], write=["Partner A"]))

    assert (
        text == "Session started. Picked session: reads Partner A and Chemistry; writes Partner A."
    )
    body = _body(declared)
    assert body["agent_session"] == CONVERSATION
    assert body["read_project_ids"] == [CHEM, A] and body["write_project_ids"] == [A]
    assert _bearer(declared) == f"Bearer {PERSON_KEY}"
    assert held.token == "bk_st_new" and held.session["mode"] == "picked"


@respx.mock
def test_start_session_read_everything():
    _active({**_session("automatic"), "agent_session": CONVERSATION})
    declared = respx.post(f"{API}/v1/sessions").respond(
        201, json=_created({**_session("read_everything"), "agent_session": CONVERSATION})
    )

    text = asyncio.run(mcp_session.start_session(read_everything=True))

    assert _body(declared)["read_everything"] is True
    assert text.startswith("Session started. Read-everything session")


@respx.mock
def test_start_session_says_each_reason_on_its_own_line():
    _active({**_session("automatic"), "agent_session": CONVERSATION})
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [_project(A), _project(CHEM)], "has_more": False}
    )
    respx.post(f"{API}/v1/sessions").respond(
        422,
        json=_refusal(
            422,
            "session_selection_invalid",
            "first",
            "Change the read or write list, or check the options with POST /v1/sessions/options.",
            reasons=[{"message": "Reason one."}, {"message": "Reason two."}],
        ),
    )

    refused = asyncio.run(mcp_session.start_session(read=[A], write=[CHEM]))

    assert refused["error_code"] == "session_selection_invalid"
    assert refused["message"] == "- Reason one.\n- Reason two."


def test_start_session_read_everything_takes_no_lists():
    _active(_session("automatic"))

    refused = asyncio.run(mcp_session.start_session(read=[A], read_everything=True))
    assert refused["error_code"] == "invalid_arguments"

    nothing = asyncio.run(mcp_session.start_session())
    assert nothing["error_code"] == "invalid_arguments"
    assert "read_everything=true" in nothing["message"]


@respx.mock
def test_start_session_without_a_conversation_id_changes_the_servers_own_session():
    _active(_session("automatic"), agent=None)
    respx.get(f"{API}/v1/sessions/current").respond(json=_session("automatic"))
    patched = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session("read_everything"))

    text = asyncio.run(mcp_session.start_session(read_everything=True))

    assert _body(patched) == {"read_everything": True}
    assert _bearer(patched) == f"Bearer {PERSON_KEY}"
    assert text.startswith("Session started. Read-everything session")


@respx.mock
def test_start_session_after_touching_a_project_is_refused_without_a_conversation_id():
    touched = _session("automatic", touched_projects=[_ref(A)])
    _active(touched, agent=None)
    respx.get(f"{API}/v1/sessions/current").respond(json=touched)
    patched = respx.patch(f"{API}/v1/sessions/{SID}")

    refused = asyncio.run(mcp_session.start_session(read_everything=True))

    assert refused["error_code"] == "session_already_started"
    assert "Partner A" in refused["message"]
    assert not patched.called


def test_start_session_without_a_conversation_id_says_it_works_as_you(monkeypatch):
    for name in ("BEAKR_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(name, raising=False)

    refused = asyncio.run(mcp_session.start_session(read=[A]))

    assert refused["error_code"] == "session_unavailable"
    assert refused["message"] == (
        "This connection has no agent session id (X-Beakr-Session), so it works as you, "
        "outside a session."
    )


@respx.mock
def test_start_session_without_a_held_session_declares_the_conversations(conversation):
    # BEAKR_MCP_SESSIONS=off: the conversation's session, by its id, as the remote does.
    declared = respx.post(f"{API}/v1/sessions").respond(201, json=_session("read_everything"))

    text = asyncio.run(mcp_session.start_session(read_everything=True))

    body = _body(declared)
    assert body["agent_session"] == CONVERSATION and body["read_everything"] is True
    assert text.startswith("Session started. Read-everything session")


@respx.mock
def test_session_status_tool_reads_the_status_route():
    _active(_session("automatic"))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session("automatic"))
    route = respx.get(f"{API}/v1/sessions/current/status").respond(json=_status())

    text = asyncio.run(mcp_session.session_status())

    assert _bearer(route) == "Bearer bk_st_first"
    assert text.splitlines()[0].startswith("Automatic session. It read Partner A")
    assert "Can't write Chemistry: This session read Partner A" in text


@respx.mock
def test_session_status_tool_outside_a_session_on_the_server():
    _active(_session("automatic"))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session("automatic"))
    respx.get(f"{API}/v1/sessions/current/status").respond(
        404, json=_refusal(404, "not_found", "This request is not in a session.")
    )

    assert asyncio.run(mcp_session.session_status()) == mcp_session.NOT_IN_SESSION


@respx.mock
def test_session_status_tool_without_a_held_session():
    respx.get(f"{API}/v1/sessions/current/status").respond(
        404, json=_refusal(404, "not_found", "This request is not in a session.")
    )
    assert asyncio.run(mcp_session.session_status()) == mcp_session.NOT_IN_SESSION


@respx.mock
def test_list_projects_shows_each_boundary_in_an_automatic_session():
    from beakr_cli.mcp_server import mcp

    _active(_session("automatic"))
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1", "name": "Octant"})
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [_project(A, SILOED), _project(CHEM, INTERNAL)], "has_more": False}
    )

    result = asyncio.run(mcp.call_tool("list_projects", {}))

    text = result.content[0].text
    assert f"  Boundary: {SILOED}" in text and f"  Boundary: {INTERNAL}" in text


@respx.mock
def test_list_projects_shows_each_boundary_in_a_picked_session():
    from beakr_cli.mcp_server import mcp

    _active(_session())
    respx.get(f"{API}/v1/sessions/current").respond(json=_session())
    respx.get(f"{API}/v1/projects/{A}").respond(json=_project(A, SILOED))
    respx.get(f"{API}/v1/projects/{CHEM}").respond(json=_project(CHEM, INTERNAL))

    result = asyncio.run(mcp.call_tool("list_projects", {}))

    text = result.content[0].text
    assert f"Partner A (id: {A}) -- read and write\n  Boundary: {SILOED}" in text
    assert f"Chemistry (id: {CHEM}) -- read\n  Boundary: {INTERNAL}" in text


# --- Lock notices -------------------------------------------------------------------------


def _locked_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"user": {}},
        headers={
            "X-Beakr-Session-Notice": quote(LOCKED_NOTICE),
            "X-Beakr-Session-Lock": "locked",
        },
    )


def test_notice_of_decodes_the_header():
    response = _locked_response(httpx.Request("GET", f"{API}/v1/me"))

    assert session_notice.notice_of(response) == LOCKED_NOTICE
    assert session_notice.notice_of(httpx.Response(200)) is None


@respx.mock
def test_a_cli_command_says_the_notice_on_stderr():
    respx.get(f"{API}/v1/projects").mock(
        return_value=httpx.Response(
            200,
            json={"data": [], "has_more": False},
            headers={"X-Beakr-Session-Notice": quote(LOCKED_NOTICE)},
        )
    )

    result = runner.invoke(app, ["projects", "ls"])

    assert result.exit_code == 0, result.output
    assert f"Session: {LOCKED_NOTICE}" in result.stderr


@respx.mock
def test_a_tool_result_opens_with_the_notice():
    from beakr_cli.mcp_server import mcp

    _active(_session("automatic"))
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1", "name": "Octant"})
    respx.get(f"{API}/v1/projects").mock(
        return_value=httpx.Response(
            200,
            json={"data": [_project(A, SILOED)], "has_more": False},
            headers={"X-Beakr-Session-Notice": quote(LOCKED_NOTICE)},
        )
    )

    result = asyncio.run(mcp.call_tool("list_projects", {}))

    assert result.content[0].text.splitlines()[0] == f"Session: {LOCKED_NOTICE}"


@respx.mock
def test_a_refused_tool_call_keeps_the_apis_words():
    from beakr_cli.mcp_server import mcp

    _active(_session("automatic"))
    message = "This session read Partner A, so it can't write to Chemistry."
    fix = "Start a new session to work in Chemistry."
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1", "name": "Octant"})
    respx.get(f"{API}/v1/projects").respond(
        403, json=_refusal(403, "session_locked_to_project", message, fix, session_id=SID)
    )

    result = asyncio.run(mcp.call_tool("list_projects", {}))

    body = json.loads(result.content[0].text)
    assert result.isError
    assert body["error_code"] == "session_locked_to_project"
    assert body["message"] == message and body["fix"] == fix


# --- The per-session log --------------------------------------------------------------


def test_trail_shows_boundary_events_beside_reads():
    from beakr_cli.session_trail import render_trail

    text = render_trail(
        {
            "data": [
                {
                    "at": "2026-11-13T21:04:11Z",
                    "op": "boundary:locked",
                    "projects": [_ref(A)],
                    "items": [],
                    "summary": "Locked to Partner A by knowledge_base sections (scope Partner A).",
                },
                {
                    "at": "2026-11-13T21:00:00Z",
                    "op": "boundary:started",
                    "projects": [],
                    "items": [],
                    "summary": "Automatic session.",
                },
            ],
            "has_more": False,
        }
    )

    lines = text.splitlines()
    assert "boundary:locked Partner A  Locked to Partner A by knowledge_base sections" in lines[1]
    assert "boundary:started  Automatic session." in lines[2]
    assert "no project" not in text
