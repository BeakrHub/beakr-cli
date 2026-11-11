"""No agent hooks on a self-hosted build (rc.9, Max's decision for Octant).

What each piece promises, one test or more each:

- ``beakr hooks install`` (Claude Code and Codex) refuses with "Agent hooks are not
  available on this deployment." and exits 1, writing nothing;
- ``beakr setup`` never offers or installs hooks, and ``--hooks`` is refused before
  anything is signed in or written;
- ``beakr context --hook`` and ``beakr compound --hook`` (what a separately installed
  Beakr Claude Code plugin's hooks call) exit 0 at once and send no request, so no
  hook opens a session or captures one;
- ``beakr hooks uninstall`` still removes hooks installed before;
- a public build keeps its hooks.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import agent_hooks, updates, write_session
from beakr_cli.commands.hooks import install_hooks_during_setup
from beakr_cli.main import app

API = "https://api.test"
UNAVAILABLE = "Agent hooks are not available on this deployment."

runner = CliRunner()


@pytest.fixture(autouse=True)
def self_hosted(monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.5.0+selfhosted.g0123456789ab")
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setattr("beakr_cli.agent_hooks._beakr_command", lambda: "/opt/bin/beakr")


def _settings(home: Path) -> Path:
    return home / ".claude" / "settings.json"


@pytest.mark.parametrize("client", ["claude", "codex", "all"])
@respx.mock
def test_hooks_install_is_refused(isolated_home: Path, client: str):
    result = runner.invoke(app, ["hooks", "install", "--client", client])

    assert result.exit_code == 1, result.output
    assert " ".join(result.output.split()) == f"Error: {UNAVAILABLE}"
    assert not _settings(isolated_home).exists()
    assert not agent_hooks.agents_block_installed()
    assert not respx.calls


@respx.mock
def test_setup_with_hooks_is_refused_before_anything(isolated_home: Path):
    (isolated_home / ".claude").mkdir()

    result = runner.invoke(app, ["setup", "--client", "claude", "--no-auth", "--hooks"])

    assert result.exit_code == 1, result.output
    assert UNAVAILABLE in result.output
    assert not _settings(isolated_home).exists()
    assert not respx.calls


def test_setup_never_offers_hooks(isolated_home: Path, monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr("beakr_cli.commands.hooks.is_interactive", lambda: True)
    monkeypatch.setattr(
        "beakr_cli.commands.hooks.confirm_if_interactive",
        lambda question, **_: asked.append(question) or True,
    )

    install_hooks_during_setup(["claude", "codex"], None)

    assert asked == []
    assert not _settings(isolated_home).exists()


@respx.mock
def test_the_context_hook_does_nothing():
    # A public build's context hook asks the API here; this one must not.
    api = respx.route(host="api.test").respond(200, json={"content": "context"})
    result = runner.invoke(
        app, ["context", "--format", "claude_md", "--project", "auto", "--hook"], input="{}"
    )

    assert result.exit_code == 0, result.output
    assert result.output == ""
    assert not api.called


@respx.mock
def test_the_compound_hook_does_nothing_and_opens_no_session(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(write_session, "ENABLED", True)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-123")
    api = respx.route(host="api.test").respond(200, json={})
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "sessionId": "s-1", "cwd": str(tmp_path)}))
    stdin = json.dumps({"transcript_path": str(transcript), "cwd": str(tmp_path)})

    for extra in ([], ["--force"]):
        result = runner.invoke(
            app,
            ["compound", "--hook", "--client", "claude-code", "--background", *extra],
            input=io.StringIO(stdin).read(),
        )
        assert result.exit_code == 0, result.output

    assert not api.called


def test_hooks_uninstall_still_removes_them(isolated_home: Path, monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.5.0")
    assert runner.invoke(app, ["hooks", "install"]).exit_code == 0
    assert agent_hooks.claude_hooks_installed()
    monkeypatch.setattr(updates, "__version__", "0.5.0+selfhosted.g0123456789ab")

    result = runner.invoke(app, ["hooks", "uninstall"])

    assert result.exit_code == 0, result.output
    assert agent_hooks.claude_hooks_installed() == []


def test_a_public_build_keeps_its_hooks(isolated_home: Path, monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.5.0")
    result = runner.invoke(app, ["hooks", "install"])
    assert result.exit_code == 0, result.output
    assert agent_hooks.claude_hooks_installed() == ["SessionStart", "Stop", "SessionEnd"]


# --- beakr codex and the capture opt-ins (Max, rc.9) -------------------------------------


def _fake_codex(monkeypatch, tmp_path: Path) -> list[list[str]]:
    """``codex`` on PATH, recording each run instead of starting it."""
    import subprocess

    from beakr_cli.commands import codex_wrapper

    runs: list[list[str]] = []
    monkeypatch.setattr(codex_wrapper, "_codex_binary", lambda: str(tmp_path / "codex"))
    monkeypatch.setattr(
        codex_wrapper.subprocess,
        "run",
        lambda args, **_: runs.append(list(args)) or subprocess.CompletedProcess(args, 0),
    )
    return runs


@respx.mock
def test_beakr_codex_is_refused_before_touching_anything(
    isolated_home: Path, tmp_path, monkeypatch
):
    runs = _fake_codex(monkeypatch, tmp_path)
    api = respx.route(host="api.test").respond(200, json={"content": "context"})

    result = runner.invoke(app, ["codex", "exec", "hello"])

    assert result.exit_code == 1, result.output
    assert " ".join(result.output.split()) == (
        "Error: `beakr codex` is not available on this deployment."
    )
    assert runs == []
    assert not api.called
    assert not agent_hooks.agents_block_installed()


def _bound(tmp_path: Path, monkeypatch) -> None:
    from beakr_cli.project_binding import write_binding

    monkeypatch.chdir(tmp_path)
    write_binding(tmp_path, {"project_id": "p1", "name": "Helios"})


def test_capture_on_is_refused_but_off_and_status_work(tmp_path, monkeypatch):
    _bound(tmp_path, monkeypatch)

    refused = runner.invoke(app, ["hooks", "capture", "on"])
    assert refused.exit_code == 1, refused.output
    assert UNAVAILABLE in refused.output

    from beakr_cli.project_binding import capture_enabled

    assert not capture_enabled(tmp_path)
    assert runner.invoke(app, ["hooks", "capture", "off"]).exit_code == 0
    status = runner.invoke(app, ["hooks", "capture", "status", "--json"])
    assert status.exit_code == 0 and json.loads(status.stdout)["capture"] is False


@respx.mock
def test_init_capture_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api = respx.route(host="api.test").respond(200, json={"id": "p1", "name": "Helios"})

    result = runner.invoke(app, ["init", "--project", "p1", "--capture"])

    assert result.exit_code == 1, result.output
    assert UNAVAILABLE in result.output
    assert not (tmp_path / ".beakr").exists()
    assert not api.called


@respx.mock
def test_importing_codex_sessions_by_hand_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    result = runner.invoke(app, ["compound", "--from-codex-sessions"])

    assert UNAVAILABLE not in result.output and "not available" not in result.output


def test_a_public_build_still_runs_codex_and_takes_capture(
    isolated_home: Path, tmp_path, monkeypatch
):
    monkeypatch.setattr(updates, "__version__", "0.5.0")
    runs = _fake_codex(monkeypatch, tmp_path)
    from beakr_cli.commands import codex_wrapper

    monkeypatch.setattr(codex_wrapper, "refresh_codex_context", lambda cwd: True)
    monkeypatch.setattr(codex_wrapper, "capture_codex_sessions", lambda cwd, started: 0)

    result = runner.invoke(app, ["codex", "exec", "hello"])
    assert result.exit_code == 0, result.output
    assert runs and runs[0][1:] == ["exec", "hello"]

    _bound(tmp_path, monkeypatch)
    captured = runner.invoke(app, ["hooks", "capture", "on"])
    assert captured.exit_code == 0, captured.output
