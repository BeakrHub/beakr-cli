"""Only a person's own typed session, in a project that opted in, is sent as interactive.

Root cause (audit R051): ``_import_and_compound_locked`` sent ``interactive:
true`` for every import, so a headless ``claude -p`` run, an Agent SDK job, a
``codex exec`` sweep or a subagent rollout all reached the engine as human
evidence for personal learning (PROSE-400). Now two things are both required:

- the project opted in to learning from sessions (``learn_from_sessions:
  true`` in ``.beakr/project.json``, separate from capture consent, off by
  default, reset when the directory is rebound to another project); and
- the host's own transcript markers classify the session as ``human``
  (Claude Code ``entrypoint: cli`` with a typed prompt; Codex ``source``
  ``cli``/``vscode``). Headless and unknown sessions are sent as false.

The value is decided at capture time and recorded on each queued retry, so a
retry never sends a session as more interactive than it was captured as.
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
from beakr_cli.project_binding import (
    LEARNING_FIELD,
    learning_enabled,
    read_binding,
    write_binding,
)
from beakr_cli.transcripts import (
    HEADLESS,
    HUMAN,
    UNKNOWN,
    classify_claude_code,
    classify_codex,
    parse_claude_code_transcript,
    parse_codex_session,
)

API = "https://api.test"
FIXTURES = Path(__file__).parent / "fixtures/transcripts"
OK = {"conversation_id": "c1", "created": True, "stored": "summary"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_learning_optin")


def _repo(tmp_path: Path, *, learning: bool | None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    binding = {"project_id": "p1", "name": "Assays", "base_url": API, "org_id": None}
    binding["capture"] = True
    if learning is not None:
        binding[LEARNING_FIELD] = learning
    write_binding(repo, binding)
    return repo


def _claude_transcript(tmp_path: Path, repo: Path, entrypoint: str | None) -> Path:
    """The Claude fixture with the markers Claude Code writes for ``entrypoint``."""
    lines = []
    for raw in (FIXTURES / "claude_code_session.jsonl").read_text().splitlines():
        raw = raw.replace("/work/repo", str(repo))
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            # The fixture keeps a malformed line on purpose; leave it as written.
            lines.append(raw)
            continue
        if entrypoint is not None and item.get("type") in ("user", "assistant"):
            item["entrypoint"] = entrypoint
            if item["type"] == "user" and not item.get("isMeta"):
                if entrypoint == "cli":
                    item["promptSource"] = "typed"
                    item["origin"] = {"kind": "human"}
                else:
                    item["promptSource"] = "sdk"
        lines.append(json.dumps(item))
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def _run_stop_hook(transcript: Path, repo: Path, *, offline: bool = False) -> list[dict]:
    with respx.mock(assert_all_called=False) as mock:
        if offline:
            imported = mock.post(f"{API}/v1/conversations/import").mock(
                side_effect=httpx.ConnectError("offline")
            )
        else:
            imported = mock.post(f"{API}/v1/conversations/import").respond(json=OK)
        mock.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
        stdin = json.dumps({"transcript_path": str(transcript), "cwd": str(repo)})
        result = CliRunner().invoke(app, ["compound", "--hook"], input=stdin)
        assert result.exit_code == 0, result.output
        return [json.loads(call.request.content) for call in imported.calls]


# ---------------------------------------------------------------------------
# What the Stop hook actually sends
# ---------------------------------------------------------------------------


def test_a_typed_cli_session_in_an_opted_in_project_is_sent_interactive(tmp_path, monkeypatch):
    repo = _repo(tmp_path, learning=True)
    monkeypatch.chdir(repo)
    bodies = _run_stop_hook(_claude_transcript(tmp_path, repo, "cli"), repo)
    assert bodies and all(body["interactive"] is True for body in bodies)


@pytest.mark.parametrize("entrypoint", ["sdk-cli", "sdk-py", "sdk-ts"])
def test_a_headless_claude_run_is_never_sent_interactive(tmp_path, monkeypatch, entrypoint):
    # `claude -p` writes entrypoint sdk-cli; the Agent SDK writes sdk-py / sdk-ts.
    repo = _repo(tmp_path, learning=True)
    monkeypatch.chdir(repo)
    bodies = _run_stop_hook(_claude_transcript(tmp_path, repo, entrypoint), repo)
    assert bodies and all(body["interactive"] is False for body in bodies)


def test_a_session_with_no_host_markers_is_never_sent_interactive(tmp_path, monkeypatch):
    repo = _repo(tmp_path, learning=True)
    monkeypatch.chdir(repo)
    bodies = _run_stop_hook(_claude_transcript(tmp_path, repo, None), repo)
    assert bodies and all(body["interactive"] is False for body in bodies)


@pytest.mark.parametrize("learning", [None, False], ids=["unset", "off"])
def test_a_typed_session_is_not_interactive_without_the_learning_opt_in(
    tmp_path, monkeypatch, learning
):
    # Capture consent alone does not let personal learning read the session.
    repo = _repo(tmp_path, learning=learning)
    monkeypatch.chdir(repo)
    bodies = _run_stop_hook(_claude_transcript(tmp_path, repo, "cli"), repo)
    assert bodies and all(body["interactive"] is False for body in bodies)


# ---------------------------------------------------------------------------
# Queued retries keep the value decided at capture time
# ---------------------------------------------------------------------------


def _flush(repo: Path) -> list[dict]:
    """Run the next hook (a fresh, already-sent transcript) so the queue flushes."""
    with respx.mock(assert_all_called=False) as mock:
        imported = mock.post(f"{API}/v1/conversations/import").respond(json=OK)
        mock.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
        from beakr_cli import compounding
        from beakr_cli.sdk import get_sdk

        with get_sdk() as sdk:
            identity = compounding.current_identity(sdk)
            assert identity is not None
            assert compounding.flush_queue(sdk, identity) is True
        return [json.loads(call.request.content) for call in imported.calls]


def test_a_queued_headless_retry_stays_false_after_learning_is_turned_on(tmp_path, monkeypatch):
    repo = _repo(tmp_path, learning=False)
    monkeypatch.chdir(repo)
    _run_stop_hook(_claude_transcript(tmp_path, repo, "sdk-cli"), repo, offline=True)
    (path,) = hook_queue.queued_files()
    queued = hook_queue.read_payload(path)
    assert queued["classification"] == HEADLESS
    assert queued["body"]["interactive"] is False

    result = CliRunner().invoke(app, ["hooks", "learning", "on", "--json"])
    assert result.exit_code == 0, result.output
    bodies = _flush(repo)
    assert bodies and all(body["interactive"] is False for body in bodies)


def test_a_queued_retry_is_not_upgraded_when_captured_before_the_opt_in(tmp_path, monkeypatch):
    # A typed session captured while learning was off stays false on retry.
    repo = _repo(tmp_path, learning=False)
    monkeypatch.chdir(repo)
    _run_stop_hook(_claude_transcript(tmp_path, repo, "cli"), repo, offline=True)
    (path,) = hook_queue.queued_files()
    assert hook_queue.read_payload(path)["classification"] == HUMAN
    CliRunner().invoke(app, ["hooks", "learning", "on"])
    bodies = _flush(repo)
    assert bodies and all(body["interactive"] is False for body in bodies)


def test_a_queued_human_retry_keeps_true_and_drops_to_false_if_learning_is_withdrawn(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path, learning=True)
    monkeypatch.chdir(repo)
    transcript = _claude_transcript(tmp_path, repo, "cli")
    _run_stop_hook(transcript, repo, offline=True)
    (path,) = hook_queue.queued_files()
    saved = hook_queue.read_payload(path)
    assert saved["body"]["interactive"] is True

    bodies = _flush(repo)
    assert bodies and all(body["interactive"] is True for body in bodies)

    # The same entry again, after the person withdrew the learning opt-in.
    hook_queue.enqueue(saved)
    CliRunner().invoke(app, ["hooks", "learning", "off"])
    bodies = _flush(repo)
    assert bodies and all(body["interactive"] is False for body in bodies)


def test_a_retry_queued_before_classification_was_recorded_is_sent_false(tmp_path, monkeypatch):
    # Entries from older CLI versions were all queued with interactive: true.
    repo = _repo(tmp_path, learning=True)
    monkeypatch.chdir(repo)
    _run_stop_hook(_claude_transcript(tmp_path, repo, "cli"), repo, offline=True)
    (path,) = hook_queue.queued_files()
    legacy = hook_queue.read_payload(path)
    path.unlink()
    legacy.pop("classification")
    hook_queue.enqueue(legacy)
    bodies = _flush(repo)
    assert bodies and all(body["interactive"] is False for body in bodies)


# ---------------------------------------------------------------------------
# Codex: exec vs interactive
# ---------------------------------------------------------------------------


def _codex_rollout(home: Path, repo: Path, name: str, meta: dict) -> Path:
    sessions = home / ".codex" / "sessions" / "2026" / "10" / "10"
    sessions.mkdir(parents=True, exist_ok=True)
    lines = (FIXTURES / "codex_rollout.jsonl").read_text().replace("/work/other", str(repo))
    out = []
    for raw in lines.splitlines():
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            out.append(raw)
            continue
        if item.get("type") == "session_meta":
            item["payload"] = {**item["payload"], "id": name, **meta}
            for key in [k for k, v in item["payload"].items() if v is None]:
                del item["payload"][key]
        out.append(json.dumps(item))
    path = sessions / f"rollout-{name}.jsonl"
    path.write_text("\n".join(out) + "\n")
    return path


def test_codex_exec_is_sent_false_and_an_interactive_codex_session_true(
    tmp_path, monkeypatch, isolated_home
):
    repo = _repo(tmp_path, learning=True)
    monkeypatch.chdir(repo)
    _codex_rollout(isolated_home, repo, "tui", {"originator": "codex-tui", "source": "cli"})
    _codex_rollout(
        isolated_home, repo, "desktop", {"originator": "Codex Desktop", "source": "vscode"}
    )
    _codex_rollout(isolated_home, repo, "exec", {"originator": "codex_exec", "source": "exec"})
    _codex_rollout(
        isolated_home,
        repo,
        "subagent",
        {"originator": "codex-tui", "source": {"subagent": {"thread_spawn": {}}}},
    )
    _codex_rollout(isolated_home, repo, "old", {"originator": "codex_cli_rs", "source": None})
    with respx.mock(assert_all_called=False) as mock:
        imported = mock.post(f"{API}/v1/conversations/import").respond(json=OK)
        mock.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
        result = CliRunner().invoke(app, ["compound", "--from-codex-sessions", "--hook"])
        assert result.exit_code == 0, result.output
        sent = {
            json.loads(c.request.content)["session_id"]: json.loads(c.request.content)[
                "interactive"
            ]
            for c in imported.calls
        }
    assert sent == {
        "tui": True,
        "desktop": True,
        "exec": False,
        "subagent": False,
        "old": False,
    }


# ---------------------------------------------------------------------------
# The opt-in itself
# ---------------------------------------------------------------------------


def test_learning_opt_in_is_separate_from_capture_and_off_by_default(tmp_path, monkeypatch):
    repo = _repo(tmp_path, learning=None)
    monkeypatch.chdir(repo)
    status = CliRunner().invoke(app, ["hooks", "learning", "status", "--json"])
    assert json.loads(status.output)["learn_from_sessions"] is False
    on = CliRunner().invoke(app, ["hooks", "learning", "on", "--json"])
    assert json.loads(on.output)["learn_from_sessions"] is True
    assert learning_enabled(repo)
    binding = read_binding(repo)
    assert binding["capture"] is True and binding[LEARNING_FIELD] is True
    off = CliRunner().invoke(app, ["hooks", "learning", "off", "--json"])
    assert json.loads(off.output)["learn_from_sessions"] is False


def test_learning_cannot_be_turned_on_in_an_unbound_directory(tmp_path, monkeypatch):
    loose = tmp_path / "loose"
    loose.mkdir()
    monkeypatch.chdir(loose)
    result = CliRunner().invoke(app, ["hooks", "learning", "on"])
    assert result.exit_code == 1
    assert "beakr init" in result.output


def _init(project_id: str, *extra: str) -> dict:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{API}/v1/projects").respond(
            json={
                "data": [
                    {"id": "p1", "name": "Assays", "org_id": "o1"},
                    {"id": "p2", "name": "Other", "org_id": "o1"},
                ],
                "has_more": False,
            }
        )
        result = CliRunner().invoke(app, ["init", "--project", project_id, *extra, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)


def test_rebinding_keeps_learning_for_the_same_project_and_resets_it_for_another(
    tmp_path, monkeypatch
):
    repo = tmp_path / "bound"
    repo.mkdir()
    monkeypatch.chdir(repo)
    assert _init("p1", "--learning").get(LEARNING_FIELD) is True
    assert _init("p1").get(LEARNING_FIELD) is True
    assert LEARNING_FIELD not in _init("p2")
    assert not learning_enabled(repo)


# ---------------------------------------------------------------------------
# The manual import
# ---------------------------------------------------------------------------


def _manual_import(transcript: Path, *extra: str) -> tuple[int, list[dict], str]:
    with respx.mock(assert_all_called=False) as mock:
        imported = mock.post(f"{API}/v1/conversations/import").respond(json=OK)
        result = CliRunner().invoke(
            app, ["conversations", "import", "--from-transcript", str(transcript), *extra]
        )
        bodies = [json.loads(c.request.content) for c in imported.calls]
        return result.exit_code, bodies, result.output


def test_manual_import_is_not_interactive_unless_asked(tmp_path, monkeypatch):
    repo = _repo(tmp_path, learning=True)
    monkeypatch.chdir(repo)
    transcript = _claude_transcript(tmp_path, repo, "cli")
    code, bodies, _ = _manual_import(transcript)
    assert code == 0 and bodies and all(b["interactive"] is False for b in bodies)


def test_manual_import_sends_interactive_when_the_person_says_so(tmp_path, monkeypatch):
    repo = _repo(tmp_path, learning=None)
    monkeypatch.chdir(repo)
    transcript = _claude_transcript(tmp_path, repo, None)
    code, bodies, _ = _manual_import(transcript, "--interactive")
    assert code == 0 and bodies and all(b["interactive"] is True for b in bodies)


def test_manual_import_refuses_interactive_for_a_headless_transcript(tmp_path, monkeypatch):
    repo = _repo(tmp_path, learning=True)
    monkeypatch.chdir(repo)
    transcript = _claude_transcript(tmp_path, repo, "sdk-cli")
    code, bodies, output = _manual_import(transcript, "--interactive")
    assert code == 2 and bodies == []
    assert "headless" in output


# ---------------------------------------------------------------------------
# The classifiers
# ---------------------------------------------------------------------------


def test_claude_classification_rules():
    assert classify_claude_code({"cli"}, {"typed"}, {"human"}) == HUMAN
    assert classify_claude_code({"cli"}, {"system"}, {"task-notification"}) == UNKNOWN
    assert classify_claude_code({"cli", "sdk-cli"}, {"typed"}, {"human"}) == HEADLESS
    assert classify_claude_code({"cli"}, {"typed", "sdk"}, {"human"}) == HEADLESS
    assert classify_claude_code(set(), set(), set()) == UNKNOWN
    assert classify_claude_code({"some-new-host"}, {"typed"}, {"human"}) == UNKNOWN


def test_codex_classification_rules():
    assert classify_codex("codex-tui", "cli") == HUMAN
    assert classify_codex("Codex Desktop", "vscode") == HUMAN
    assert classify_codex("codex_exec", "exec") == HEADLESS
    assert classify_codex("codex_exec", None) == HEADLESS
    assert classify_codex("codex-tui", {"subagent": {}}) == HEADLESS
    assert classify_codex("codex-tui", "mcp") == HEADLESS
    assert classify_codex("codex_cli_rs", None) == UNKNOWN


def test_parsers_record_the_classification(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    assert (
        parse_claude_code_transcript(_claude_transcript(tmp_path, repo, "cli")).classification
        == HUMAN
    )
    assert parse_codex_session(FIXTURES / "codex_rollout.jsonl").classification == UNKNOWN
    assert parse_codex_session(FIXTURES / "codex_legacy.jsonl").classification == UNKNOWN
