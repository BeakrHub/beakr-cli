"""Automatic session capture needs the repository's own consent (spec step 8.1 item 3).

Root cause (audit C073): the Stop/SessionEnd hooks imported a transcript from any
directory once the hooks were installed globally; the project binding only chose
where the import landed, it never authorized it. Consent is now ``capture: true``
in the repository's ``.beakr/project.json``; without it no automatic path sends
or queues anything, while a deliberate import still works.
"""

from __future__ import annotations

import json
from pathlib import Path

import respx
from typer.testing import CliRunner

from beakr_cli import compounding, hook_queue
from beakr_cli.commands import memory
from beakr_cli.hook_runtime import hooks_log_path
from beakr_cli.main import app
from beakr_cli.project_binding import capture_enabled, set_capture, write_binding

API = "https://api.test"
FIXTURE = Path(__file__).parent / "fixtures/transcripts/claude_code_session.jsonl"


def _repo(tmp_path: Path, *, capture: bool | None, base_url: str = API) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    binding = {"project_id": "p1", "name": "Assays", "base_url": base_url, "org_id": None}
    if capture is not None:
        binding["capture"] = capture
    write_binding(repo, binding)
    return repo


def _transcript(tmp_path: Path, repo: Path) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_text(FIXTURE.read_text().replace("/work/repo", str(repo)))
    return path


def _env(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_consent")


def _run_stop_hook(tmp_path: Path, repo: Path) -> tuple[bool, bool]:
    transcript = _transcript(tmp_path, repo)
    with respx.mock(assert_all_called=False) as mock:
        imported = mock.post(f"{API}/v1/conversations/import").respond(
            json={"conversation_id": "c1", "created": True, "stored": "summary"}
        )
        compounded = mock.post(f"{API}/v1/compound").respond(
            202, json={"status": "queued", "conversation_id": "c1"}
        )
        stdin = json.dumps({"transcript_path": str(transcript), "cwd": str(repo)})
        result = CliRunner().invoke(app, ["compound", "--hook"], input=stdin)
        assert result.exit_code == 0, result.output
        return imported.called, compounded.called


def test_a_consenting_repository_is_captured(tmp_path, monkeypatch):
    _env(monkeypatch)
    repo = _repo(tmp_path, capture=True)
    monkeypatch.chdir(repo)
    assert _run_stop_hook(tmp_path, repo) == (True, True)


def test_a_bound_repository_without_consent_is_not_captured(tmp_path, monkeypatch):
    _env(monkeypatch)
    repo = _repo(tmp_path, capture=None)
    monkeypatch.chdir(repo)
    assert _run_stop_hook(tmp_path, repo) == (False, False)
    assert "capture is off for this project" in hooks_log_path().read_text()


def test_turning_capture_on_with_the_command_allows_capture(tmp_path, monkeypatch):
    _env(monkeypatch)
    repo = _repo(tmp_path, capture=None)
    monkeypatch.chdir(repo)
    turned_on = CliRunner().invoke(app, ["hooks", "capture", "on", "--json"])
    assert turned_on.exit_code == 0, turned_on.output
    assert json.loads(turned_on.output)["capture"] is True
    assert _run_stop_hook(tmp_path, repo) == (True, True)
    turned_off = CliRunner().invoke(app, ["hooks", "capture", "off", "--json"])
    assert json.loads(turned_off.output)["capture"] is False
    assert not capture_enabled(repo)


def test_consent_made_for_another_api_does_not_apply(tmp_path, monkeypatch):
    _env(monkeypatch)
    repo = _repo(tmp_path, capture=True, base_url="https://other.example")
    monkeypatch.chdir(repo)
    assert _run_stop_hook(tmp_path, repo) == (False, False)


def test_capture_cannot_be_turned_on_in_an_unbound_directory(tmp_path, monkeypatch):
    unbound = tmp_path / "loose"
    unbound.mkdir()
    monkeypatch.chdir(unbound)
    result = CliRunner().invoke(app, ["hooks", "capture", "on"])
    assert result.exit_code == 1
    assert "beakr init" in result.output


def test_a_deliberate_import_does_not_need_consent(tmp_path, monkeypatch):
    _env(monkeypatch)
    repo = _repo(tmp_path, capture=None)
    monkeypatch.chdir(repo)
    transcript = _transcript(tmp_path, repo)
    with respx.mock(assert_all_called=False) as mock:
        imported = mock.post(f"{API}/v1/conversations/import").respond(
            json={"conversation_id": "c1", "created": True, "stored": "summary"}
        )
        mock.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
        result = CliRunner().invoke(app, ["compound", "--from-transcript", str(transcript)])
    assert result.exit_code == 0, result.output
    assert imported.called


def _session_start(monkeypatch, cwd: Path) -> str:
    _env(monkeypatch)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{API}/v1/context").respond(json={"content": "# Org\nFacts."})
        result = CliRunner().invoke(
            app,
            ["context", "--format", "claude_md", "--hook"],
            input=json.dumps({"cwd": str(cwd)}),
        )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["hookSpecificOutput"]["additionalContext"]


def test_the_session_start_notice_says_whether_capture_is_on(tmp_path, monkeypatch):
    repo = _repo(tmp_path, capture=None)
    monkeypatch.chdir(repo)
    assert memory.CAPTURE_OFF_NOTICE in _session_start(monkeypatch, repo)
    set_capture(repo, True)
    assert memory.CAPTURE_NOTICE in _session_start(monkeypatch, repo)


def test_queued_retries_are_dropped_once_consent_is_withdrawn(tmp_path, monkeypatch):
    repo = _repo(tmp_path, capture=True)
    identity = compounding.HookIdentity(origin=API, principal="u1")
    body = {"client": "claude-code", "session_id": "s1", "messages": []}
    consented = hook_queue.enqueue(
        {"key": "k1", "identity": identity.as_dict(), "body": body, "cwd": str(repo)}
    )
    legacy = hook_queue.enqueue({"key": "k2", "identity": identity.as_dict(), "body": body})
    set_capture(repo, False)

    sent: list[dict] = []

    class Conversations:
        def import_(self, **kwargs):
            sent.append(kwargs)
            return {"conversation_id": "c1"}

    class Sdk:
        conversations = Conversations()

    assert compounding.flush_queue(Sdk(), identity) is True
    assert sent == []
    assert consented is not None and not consented.exists()
    assert legacy is not None and not legacy.exists()


def test_the_detached_child_keeps_the_sessions_directory():
    args = memory._child_args(
        None, Path("/tmp/s.jsonl"), "claude-code", False, None, None, True, False, 300, "/work/a"
    )
    assert args[args.index("--session-cwd") + 1] == "/work/a"
    assert "--hook" in args and "--detached-child" in args


def test_init_keeps_consent_for_the_same_project_and_resets_it_for_another(tmp_path, monkeypatch):
    _env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    projects = {
        "data": [
            {"id": "p1", "name": "Assays", "org_id": "o1"},
            {"id": "p2", "name": "Pilot", "org_id": "o1"},
        ],
        "next_cursor": None,
        "has_more": False,
    }

    def init(*args: str) -> dict:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{API}/v1/projects").respond(json=projects)
            result = CliRunner().invoke(app, ["init", *args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    assert init("--project", "p1", "--capture").get("capture") is True
    assert init("--project", "p1").get("capture") is True
    assert init("--project", "p2").get("capture") is None
    assert not capture_enabled(repo)
