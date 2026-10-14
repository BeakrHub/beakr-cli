"""The Stop hook sends each message's ``author``, live and from the retry queue.

Root cause (Codex C088): the parsers stripped Claude Code's per-record markers,
so a background task's report in a person's own session reached the engine as
a plain user turn and became quotable as their words. Now a host-written
record is never sent, and every message carries ``author`` so the engine can
cite only ``human`` turns; a queued retry sends the authors it was captured with.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import hook_queue
from beakr_cli.main import app
from beakr_cli.project_binding import LEARNING_FIELD, write_binding

API = "https://api.test"
OK = {"conversation_id": "c1", "created": True, "stored": "summary"}
TYPED = "Please make the export job retry on timeouts."
REPORT = "I am migrating the billing database to a new ledger schema this month."


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_authorship")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_binding(
        repo,
        {
            "project_id": "p1",
            "name": "Assays",
            "base_url": API,
            "org_id": None,
            "capture": True,
            LEARNING_FIELD: True,
        },
    )
    return repo


def _record(kind: str, content: str, repo: Path, **extra: object) -> dict:
    return {
        "type": kind,
        "entrypoint": "cli",
        "sessionId": "mixed-session",
        "cwd": str(repo),
        "message": {"role": kind, "content": content},
        **extra,
    }


def _mixed_transcript(tmp_path: Path, repo: Path) -> Path:
    records = [
        _record("user", TYPED, repo, promptSource="typed", origin={"kind": "human"}),
        _record("assistant", "Started a background task.", repo),
        _record(
            "user",
            f"<task-notification><result>{REPORT}</result></task-notification>",
            repo,
            promptSource="system",
            origin={"kind": "task-notification"},
        ),
        _record("assistant", "The task finished.", repo),
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _stop_hook(transcript: Path, repo: Path, *, offline: bool) -> list[dict]:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{API}/v1/conversations/import")
        if offline:
            route.mock(side_effect=httpx.ConnectError("offline"))
        else:
            route.respond(json=OK)
        mock.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
        stdin = json.dumps({"transcript_path": str(transcript), "cwd": str(repo)})
        result = CliRunner().invoke(app, ["compound", "--hook"], input=stdin)
        assert result.exit_code == 0, result.output
        return [json.loads(call.request.content) for call in route.calls]


def _sent(bodies: list[dict]) -> list[tuple[str, str, str]]:
    return [(m["role"], m["author"], m["content"]) for b in bodies for m in b["messages"]]


EXPECTED = [
    ("user", "human", TYPED),
    ("assistant", "agent", "Started a background task."),
    ("assistant", "agent", "The task finished."),
]


def test_the_hook_sends_authors_and_never_the_task_report(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    bodies = _stop_hook(_mixed_transcript(tmp_path, repo), repo, offline=False)
    assert bodies and all(body["interactive"] is True for body in bodies)
    assert _sent(bodies) == EXPECTED
    assert REPORT not in json.dumps(bodies)


def test_a_queued_retry_sends_the_authors_it_was_captured_with(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    _stop_hook(_mixed_transcript(tmp_path, repo), repo, offline=True)
    (path,) = hook_queue.queued_files()
    queued = hook_queue.read_payload(path)
    assert _sent([queued["body"]]) == EXPECTED

    with respx.mock(assert_all_called=False) as mock:
        imported = mock.post(f"{API}/v1/conversations/import").respond(json=OK)
        mock.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
        from beakr_cli import compounding
        from beakr_cli.sdk import get_sdk

        with get_sdk() as sdk:
            identity = compounding.current_identity(sdk)
            assert identity is not None
            assert compounding.flush_queue(sdk, identity) is True
        bodies = [json.loads(call.request.content) for call in imported.calls]
    assert _sent(bodies) == EXPECTED
    assert REPORT not in json.dumps(bodies)
