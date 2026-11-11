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
