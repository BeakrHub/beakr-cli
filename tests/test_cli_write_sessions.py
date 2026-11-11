"""Writes into a project run in a session the person chose (beakr_cli/write_session.py).

What the gate promises, one test or more each:

- every published command is classified: it writes into a project (gated) or not;
- reads never ask; a write with ``BEAKR_SCOPE_SESSION`` uses that session's token;
- without one, at a terminal, the picker opens, the chosen session's token makes
  the write, and the terminal keeps the session so its next write does not ask;
- not at a terminal, the session starts pending, the command prints the picker
  link and waits for the choice; on timeout it exits 6 with the link and the id;
- a project the session may not write is refused with its name and the fix;
- writes the API takes from no session token run with the person's credential,
  after the CLI checks the project is in the session's write list;
- service accounts, installations without sessions and session tokens run as before;
- a hook never asks or waits.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import click
import httpx
import pytest
import respx
import typer.main
from typer.testing import CliRunner

from beakr_cli import scope_session, write_session
from beakr_cli.main import app as published
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import TokenState

pytestmark = pytest.mark.real_write_sessions

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
PERSON_KEY = "bk_live_person_key_for_tests"
FILE = "f-1"

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.setattr(write_session, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(write_session, "_interactive", lambda: False)


def _later(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _session(status: str = "active", *, read=(A, B), write=(A,)) -> dict:
    names = {A: "Helios", B: "Assays"}
    chosen = status != "pending"
    return {
        "id": SID,
        "status": status,
        "folder": None,
        "remember_folder": False,
        "read_projects": [{"id": p, "name": names[p]} for p in read] if chosen else [],
        "write_projects": [{"id": p, "name": names[p]} for p in write] if chosen else [],
        "picker_url": f"https://app.test/sessions/{SID}",
    }


def _created(status: str = "pending", **kw) -> dict:
    return {
        **_session(status, **kw),
        "token": {"token": "bk_st_first", "token_type": "bearer", "expires_at": _later()},
    }


def _bearer(route) -> str:
    return route.calls.last.request.headers["authorization"]


def _refusal(code: str, message: str) -> dict:
    """The API's error envelope (``error``, and FastAPI's ``detail``)."""
    return {
        "detail": {"code": code, "message": message},
        "error": {"code": code, "message": message, "fix": None},
    }


def _remember(folder, read: list[str], write: list[str]) -> None:
    """The folder's remembered choice, as the picker's "remember" leaves it."""
    binding = folder / ".beakr" / "project.json"
    binding.parent.mkdir()
    scope = {"read_project_ids": read, "write_project_ids": write}
    binding.write_text(json.dumps({"base_url": API, scope_session.BINDING_FIELD: scope}))


def _text(result) -> str:
    return " ".join(result.output.split())


def _delete():
    return respx.delete(f"{API}/v1/artifacts/{FILE}").respond(204)


def _rm(*extra: str):
    return runner.invoke(app, ["files", "rm", FILE, "--yes", *extra])


# --- Classification ---------------------------------------------------------------------


def _published_paths() -> set[str]:
    paths: set[str] = set()

    def walk(command: click.Command, prefix: str) -> None:
        if isinstance(command, click.Group):
            for name in command.list_commands(click.Context(command)):
                if not prefix and name == "api":
                    continue  # generated per operation: classified by operation_mode
                walk(command.get_command(click.Context(command), name), f"{prefix}{name} ")
        else:
            paths.add(prefix.strip())

    walk(typer.main.get_command(published), "")
    return paths


def test_every_published_command_is_classified():
    paths = _published_paths()
    gated, ungated = set(write_session.WRITE_COMMANDS), set(write_session.NOT_GATED)
    assert not gated & ungated
    assert paths - gated - ungated == set(), "classify new commands in write_session.py"
    assert (gated | ungated) - paths == set(), "classified commands that do not exist"


def test_the_gated_commands_run_through_the_gate():
    root = typer.main.get_command(published)
    for path in write_session.WRITE_COMMANDS:
        command: click.Command = root
        for word in path.split():
            command = command.get_command(click.Context(command), word)
        assert getattr(command, "beakr_write_command", None) == path


def test_api_operations_are_gated_by_what_they_write():
    def mode(op: str, scope: str | None) -> str | None:
        return write_session.operation_mode({"op": op, "scope": scope})

    assert mode("POST /v1/knowledge/wiki/write", "kb:write") == write_session.TOKEN
    assert mode("DELETE /v1/artifacts/{artifact_id}", "uploads:write") == write_session.TOKEN
    # A file's Knowledge takes the session's token (project isolation); its approval
    # is a person's own act, never the session's.
    approve = "POST /v1/artifacts/{artifact_id}/knowledge/approve"
    assert mode(approve, "proposals:accept") == write_session.PERSON
    assert mode("POST /v1/artifacts/{artifact_id}/knowledge", "kb:write") == write_session.TOKEN
    assert mode("POST /v1/conversations/import", "conversations:write") == write_session.CHECKED
    assert mode("GET /v1/knowledge/wiki/pages", "kb:read") is None
    assert mode("POST /v1/projects", "projects:admin") is None
    assert mode("POST /v1/skills", "skills:write") is None


# --- Reads, and a named session ---------------------------------------------------------


@respx.mock
def test_a_read_never_asks_for_a_session():
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created())
    respx.get(f"{API}/v1/artifacts/{FILE}").respond(json={"id": FILE, "name": "a.pdf"})
    result = runner.invoke(app, ["files", "show", FILE, "--json"])
    assert result.exit_code == 0, result.output
    assert not started.called


@respx.mock
def test_a_write_in_a_named_session_uses_its_token(monkeypatch):
    scope_session.save_state(TokenState(SID, "bk_st_shell", _later()))
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created())
    read = respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    deleted = _delete()

    result = _rm()

    assert result.exit_code == 0, result.output
    assert not started.called
    assert _bearer(read) == f"Bearer {PERSON_KEY}"
    assert _bearer(deleted) == "Bearer bk_st_shell"


@respx.mock
def test_a_named_session_that_ended_writes_nothing(monkeypatch):
    scope_session.save_state(TokenState(SID, "bk_st_shell", _later()))
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("ended"))
    deleted = _delete()

    result = _rm()

    assert result.exit_code != 0
    assert "has ended; nothing was written" in _text(result) and not deleted.called


# --- At a terminal: the picker ----------------------------------------------------------


@respx.mock
def test_at_a_terminal_the_picker_opens_and_the_terminal_keeps_the_session(monkeypatch):
    monkeypatch.setattr(write_session, "_interactive", lambda: True)
    shown: list[tuple] = []

    def pick(projects, read, write, *, picker_url=None):
        shown.append(([p["id"] for p in projects], read, write))
        assert picker_url == f"https://app.test/sessions/{SID}"
        return [A, B], [A]

    monkeypatch.setattr("beakr_cli.commands.session.pick", pick)
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created("pending"))
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": A, "name": "Helios"}, {"id": B, "name": "Assays"}]}
    )
    chosen = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session())
    deleted = _delete()

    result = _rm()

    assert result.exit_code == 0, result.output
    assert _bearer(started) == f"Bearer {PERSON_KEY}"
    assert json.loads(chosen.calls.last.request.content) == {
        "read_project_ids": [A, B],
        "write_project_ids": [A],
    }
    assert _bearer(chosen) == f"Bearer {PERSON_KEY}"
    assert _bearer(deleted) == "Bearer bk_st_first"
    assert shown == [([A, B], [], [])]
    assert f"Session {SID}: writes Helios" in result.stderr
    assert f"export {scope_session.SESSION_ENV}={SID}" in result.stderr
    assert "bk_st_first" not in result.output
    assert scope_session.terminal_session_id() == SID

    # The next write in this terminal reuses it without asking.
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    again = _rm()
    assert again.exit_code == 0, again.output
    assert started.call_count == 1 and chosen.call_count == 1
    assert _bearer(deleted) == "Bearer bk_st_first"
    assert "writes Helios" not in again.stderr


@respx.mock
def test_the_picker_starts_from_the_folders_remembered_choice(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _remember(tmp_path, [A, B], [B])
    monkeypatch.setattr(write_session, "_interactive", lambda: True)
    shown: list[tuple] = []

    def pick(projects, read, write, *, picker_url=None):
        shown.append((read, write))
        return read, write

    monkeypatch.setattr("beakr_cli.commands.session.pick", pick)
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created("pending"))
    respx.get(f"{API}/v1/projects").respond(json={"data": [{"id": A}, {"id": B}]})
    respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session(write=(B,)))
    _delete()

    assert _rm().exit_code == 0
    assert shown == [([A, B], [B])]
    # At a terminal the session starts pending: the picker shows the choice.
    assert "read_project_ids" not in json.loads(started.calls.last.request.content)


@respx.mock
def test_cancelling_the_picker_ends_the_session_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(write_session, "_interactive", lambda: True)
    monkeypatch.setattr("beakr_cli.commands.session.pick", lambda *_a, **_k: None)
    respx.post(f"{API}/v1/sessions").respond(201, json=_created("pending"))
    respx.get(f"{API}/v1/projects").respond(json={"data": [{"id": A}]})
    ended = respx.post(f"{API}/v1/sessions/{SID}/end").respond(json=_session("ended"))
    deleted = _delete()

    result = _rm()

    assert result.exit_code == 1
    assert "nothing was written" in _text(result)
    assert ended.called and not deleted.called
    assert scope_session.load_state(SID) is None
    assert scope_session.terminal_session_id() is None


# --- Not at a terminal: the link, and a bounded wait ------------------------------------


@respx.mock
def test_not_at_a_terminal_it_prints_the_link_and_waits_for_the_choice():
    respx.post(f"{API}/v1/sessions").respond(201, json=_created("pending"))
    polled = respx.get(f"{API}/v1/sessions/{SID}").mock(
        side_effect=[
            httpx.Response(200, json=_session("pending")),
            httpx.Response(200, json=_session("pending")),
            httpx.Response(200, json=_session()),
        ]
    )
    deleted = _delete()

    result = _rm()

    assert result.exit_code == 0, result.output
    assert f"https://app.test/sessions/{SID}" in result.stderr
    assert "Waiting up to 5 minute(s)" in result.stderr
    assert polled.call_count == 3
    assert _bearer(deleted) == "Bearer bk_st_first"
    assert scope_session.terminal_session_id() == SID


@respx.mock
def test_waiting_times_out_with_the_link_and_the_session_id(monkeypatch):
    monkeypatch.setattr(write_session, "WAIT_SECONDS", 0.0)
    respx.post(f"{API}/v1/sessions").respond(201, json=_created("pending"))
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("pending"))
    deleted = _delete()

    result = _rm("--json")

    assert result.exit_code == 6
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "session_needs_selection"
    assert f"https://app.test/sessions/{SID}" in error["fix"]
    assert f"{scope_session.SESSION_ENV}={SID}" in error["fix"]
    assert not deleted.called
    # The next run in this terminal resumes the same pending session.
    assert scope_session.terminal_session_id() == SID


@respx.mock
def test_a_rerun_resumes_the_pending_session_instead_of_starting_another():
    scope_session.save_state(TokenState(SID, "bk_st_first", _later()))
    scope_session.remember_terminal(SID)
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created())
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    deleted = _delete()

    assert _rm().exit_code == 0
    assert not started.called
    assert _bearer(deleted) == "Bearer bk_st_first"


@respx.mock
def test_a_remembered_folder_choice_starts_the_session_without_waiting(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _remember(tmp_path, [A], [A])
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created("active", read=(A,)))
    deleted = _delete()

    result = _rm()

    assert result.exit_code == 0, result.output
    sent = json.loads(started.calls.last.request.content)
    assert sent["read_project_ids"] == [A] and sent["write_project_ids"] == [A]
    assert "Waiting" not in result.stderr
    assert _bearer(deleted) == "Bearer bk_st_first"


# --- A project the session may not write ------------------------------------------------


def _kept_session():
    scope_session.save_state(TokenState(SID, "bk_st_first", _later()))
    scope_session.remember_terminal(SID)
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session(read=(A, B), write=(A,)))


@respx.mock
def test_a_project_outside_the_write_list_is_refused_by_name_before_any_write():
    _kept_session()
    wrote = respx.post(f"{API}/v1/knowledge/wiki/write").respond(json={})

    result = runner.invoke(
        app,
        ["kb", "add-fact", "Page", "--attribute", "a", "--value", "v", "--rationale", "r"]
        + ["--evidence", "doc.pdf", "--scope", B],
    )

    assert result.exit_code == 3
    assert f"Session {SID} may not write to Assays" in _text(result)
    assert "It may write: Helios" in _text(result)
    assert f"beakr session edit {SID}" in _text(result)
    assert not wrote.called


@respx.mock
def test_the_apis_write_refusal_names_the_project_and_the_fix():
    _kept_session()
    respx.delete(f"{API}/v1/artifacts/{FILE}").respond(
        403,
        json=_refusal(
            "session_write_scope", f"This session may read but not write {B}. It may write {A}."
        ),
    )

    result = _rm()

    assert result.exit_code == 3
    assert "may not write to Assays" in _text(result)
    assert f"beakr session edit {SID}" in _text(result)


# --- Writes the API takes from no session token -----------------------------------------


@respx.mock
def test_a_checked_write_is_sent_with_the_persons_credential_inside_the_session(tmp_path):
    _kept_session()
    body = tmp_path / "c.json"
    body.write_text(json.dumps({"session_id": "s1", "messages": [{"role": "user", "text": "x"}]}))
    imported = respx.post(f"{API}/v1/conversations/import").respond(
        json={"conversation_id": "c1", "created": True, "stored": "summary"}
    )

    result = runner.invoke(
        app, ["conversations", "import", "--file", str(body), "-P", A, "--summary", "s"]
    )

    assert result.exit_code == 0, result.output
    assert _bearer(imported) == f"Bearer {PERSON_KEY}"

    refused = runner.invoke(
        app, ["conversations", "import", "--file", str(body), "-P", B, "--summary", "s"]
    )
    assert refused.exit_code == 3
    assert "may not write to Assays" in _text(refused)
    assert imported.call_count == 1


@respx.mock
def test_approving_a_file_checks_the_files_project():
    _kept_session()
    respx.get(f"{API}/v1/artifacts/{FILE}").respond(json={"id": FILE, "project_id": B})
    approved = respx.post(f"{API}/v1/artifacts/{FILE}/knowledge/approve").respond(json={})

    result = runner.invoke(app, ["files", "approve", FILE])

    assert result.exit_code == 3
    assert "may not write to Assays" in _text(result)
    assert not approved.called


@respx.mock
def test_approving_a_file_sends_the_persons_own_key_joined_to_no_session(monkeypatch):
    """The API refuses a session token here (an agent's session never approves its own
    files): the person's key, without X-Beakr-Session, after checking the project."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-123")
    _kept_session()
    scope_session.remember_terminal(SID, "agent:conv-123")
    respx.get(f"{API}/v1/artifacts/{FILE}").respond(json={"id": FILE, "project_id": A})
    approved = respx.post(f"{API}/v1/artifacts/{FILE}/knowledge/approve").respond(json={})

    result = runner.invoke(app, ["files", "approve", FILE])

    assert result.exit_code == 0, result.output
    request = approved.calls.last.request
    assert request.headers["authorization"] == f"Bearer {PERSON_KEY}"
    assert "x-beakr-session" not in request.headers


# --- Where no session can be held -------------------------------------------------------


@respx.mock
def test_without_sessions_on_the_installation_writes_run_as_before():
    respx.post(f"{API}/v1/sessions").respond(404, json={"detail": "Not Found"})
    deleted = _delete()
    result = _rm()
    assert result.exit_code == 0, result.output
    assert _bearer(deleted) == f"Bearer {PERSON_KEY}"


@respx.mock
def test_a_credential_the_api_refuses_a_session_writes_as_before():
    respx.post(f"{API}/v1/sessions").respond(
        403,
        json=_refusal(
            "insufficient_scope",
            "Sessions belong to a person; a service account, external user or end-user "
            "token cannot hold one.",
        ),
    )
    deleted = _delete()
    result = _rm()
    assert result.exit_code == 0, result.output
    assert _bearer(deleted) == f"Bearer {PERSON_KEY}"


@respx.mock
def test_a_service_accounts_file_never_asks(monkeypatch, tmp_path):
    from beakr_sdk.credentials import SERVICE_ACCOUNT_FILE_ENV

    monkeypatch.delenv("BEAKR_API_KEY")
    account = tmp_path / "sa.json"
    account.write_text(
        json.dumps({"type": "beakr_service_account", "api_key": "bk_live_sa", "base_url": API})
    )
    monkeypatch.setenv(SERVICE_ACCOUNT_FILE_ENV, str(account))
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created())
    deleted = _delete()

    result = _rm()

    assert result.exit_code == 0, result.output
    assert not started.called
    assert _bearer(deleted) == "Bearer bk_live_sa"


@respx.mock
def test_a_session_token_as_the_credential_never_asks(monkeypatch):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_st_given")
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created())
    deleted = _delete()
    assert _rm().exit_code == 0
    assert not started.called
    assert _bearer(deleted) == "Bearer bk_st_given"


# --- Hooks ------------------------------------------------------------------------------


def _hook(project: str | None):
    """Run the gate as ``beakr compound --hook`` does, bound to ``project``; what the write saw."""
    seen: list[tuple[str | None, str | None]] = []

    def invoke() -> str:
        seen.append((scope_session.shell_session_id(), scope_session.shell_token()))
        return "compounded"

    result = write_session.run_gated(
        "compound",
        write_session.CHECKED,
        {"hook": True},
        invoke,
        targets=lambda: [project] if project else None,
    )
    return result, seen


def _hooks_log() -> str:
    from beakr_cli.hook_runtime import hooks_log_path

    path = hooks_log_path()
    return path.read_text() if path.exists() else ""


@respx.mock
def test_a_hook_without_a_session_compounds_into_the_bound_project_as_before():
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created())

    result, seen = _hook(B)

    assert result == "compounded"
    # The person's own credential, no session: the folder's binding chose the project.
    assert seen == [(None, None)]
    assert not started.called
    assert "skipped" not in _hooks_log()


@respx.mock
def test_a_hook_in_an_open_session_compounds_into_a_project_it_may_write():
    _kept_session()
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created())

    result, seen = _hook(A)

    assert result == "compounded"
    # In the session (a detached child inherits it), sent with the person's credential.
    assert seen == [(SID, None)]
    assert not started.called
    assert scope_session.shell_session_id() is None


@respx.mock
def test_a_hook_in_an_open_session_skips_a_project_it_may_not_write():
    _kept_session()

    result, seen = _hook(B)

    assert result is None and seen == []
    log = _hooks_log()
    assert f"skipped. Session {SID} may not write Assays" in log
    assert f"beakr session edit {SID}" in log


@respx.mock
def test_a_hook_never_waits_on_a_session_whose_projects_are_not_chosen(monkeypatch):
    scope_session.save_state(TokenState(SID, "bk_st_first", _later()))
    scope_session.remember_terminal(SID)
    polled = respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("pending"))

    result, seen = _hook(A)

    assert result is None and seen == []
    assert polled.call_count == 1
    assert "its projects are not chosen yet" in _hooks_log()


@respx.mock
def test_the_compound_hook_command_never_starts_a_session(tmp_path):
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_created())
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")

    result = runner.invoke(
        app,
        ["compound", "--hook", "--from-transcript", str(transcript)],
        input=json.dumps({"transcript_path": str(transcript)}),
    )

    assert result.exit_code == 0, result.output
    assert not started.called
