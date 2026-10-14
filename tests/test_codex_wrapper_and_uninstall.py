"""``beakr codex`` refreshes context before each session and captures after it (spec step
8.1 item 2), and ``beakr setup --uninstall`` removes every hook setup installed (audit C074).

Root causes: Codex has no hooks, so its AGENTS.md context was only as fresh as the
last manual ``beakr hooks install --client codex``; and ``setup --uninstall`` removed
skills and MCP but left the session hooks, so capture kept running after uninstall.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import respx
from typer.testing import CliRunner

from beakr_cli import agent_hooks
from beakr_cli.commands import codex_wrapper, install
from beakr_cli.main import app
from beakr_cli.project_binding import write_binding

API = "https://api.test"
ROLLOUT = Path(__file__).parent / "fixtures/transcripts/codex_rollout.jsonl"


def _fake_codex(tmp_path: Path, repo: Path, *, exit_code: int = 0) -> Path:
    """A stand-in `codex` that records its argv and writes one session rollout."""
    codex_home = Path(os.environ["CODEX_HOME"])
    rollout = ROLLOUT.read_text().replace("/work/other", str(repo))
    script = tmp_path / "codex"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{tmp_path}/argv.txt"\n'
        f'mkdir -p "{codex_home}/sessions/2026/10/10"\n'
        f"cat > \"{codex_home}/sessions/2026/10/10/rollout-wrapper.jsonl\" <<'EOF'\n"
        f"{rollout}\nEOF\n"
        f"exit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _setup(tmp_path, monkeypatch, *, capture: bool) -> Path:
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_wrapper")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    binding = {"project_id": "p1", "name": "Assays", "base_url": API, "org_id": None}
    if capture:
        binding["capture"] = True
    write_binding(repo, binding)
    monkeypatch.chdir(repo)
    return repo


def _run_wrapper(tmp_path, monkeypatch, repo, *, exit_code=0, context_status=200):
    monkeypatch.setenv(
        codex_wrapper.CODEX_BIN_ENV, str(_fake_codex(tmp_path, repo, exit_code=exit_code))
    )
    with respx.mock(assert_all_called=False) as mock:
        context = mock.post(f"{API}/v1/context").respond(
            context_status, json={"content": "# Assays\nFresh facts."}
        )
        imported = mock.post(f"{API}/v1/conversations/import").respond(
            json={"conversation_id": "c1", "created": True, "stored": "summary"}
        )
        mock.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
        result = CliRunner().invoke(app, ["codex", "exec", "--full-auto", "fix the test"])
    return result, context, imported


def test_the_wrapper_refreshes_context_and_passes_codex_its_arguments(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch, capture=False)
    result, context, _ = _run_wrapper(tmp_path, monkeypatch, repo)
    assert result.exit_code == 0, result.output
    assert context.called
    sent = json.loads(context.calls[0].request.content)
    assert sent["format"] == "agents_md" and sent["project_id"] == "p1"
    assert "Fresh facts." in agent_hooks.codex_agents_path().read_text()
    argv = (tmp_path / "argv.txt").read_text().split("\n")
    assert argv[:3] == ["exec", "--full-auto", "fix the test"]


def test_the_session_is_captured_only_when_the_repository_consents(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch, capture=False)
    _, _, imported = _run_wrapper(tmp_path, monkeypatch, repo)
    assert not imported.called

    other = tmp_path / "consenting"
    other.mkdir()
    repo2 = _setup(other, monkeypatch, capture=True)
    _, _, imported = _run_wrapper(other, monkeypatch, repo2)
    assert imported.called


def test_codex_exit_code_is_kept_and_a_failed_refresh_never_blocks_codex(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch, capture=False)
    result, context, _ = _run_wrapper(tmp_path, monkeypatch, repo, exit_code=3, context_status=503)
    assert context.called
    assert result.exit_code == 3
    assert (tmp_path / "argv.txt").exists(), "Codex must still run when the refresh fails"


def _setup_assets(monkeypatch) -> None:
    monkeypatch.setattr(install, "_claude_cli", lambda: None)
    monkeypatch.setattr(install, "_beakr_command", lambda: "/opt/test/beakr")
    monkeypatch.setattr(agent_hooks, "_beakr_command", lambda: "/opt/test/beakr")
    monkeypatch.setattr(install, "_assets_root", lambda: Path(__file__).parents[1] / ".claude")


def test_setup_uninstall_keeps_foreign_hooks_at_either_scope(tmp_path, monkeypatch):
    _setup_assets(monkeypatch)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(app, ["setup", "--client", "claude", "--no-auth", "--hooks"]).exit_code == 0
    )
    settings = agent_hooks.claude_settings_path()
    data = json.loads(settings.read_text())
    foreign = {"hooks": [{"type": "command", "command": "/usr/local/bin/lint-on-stop"}]}
    data["hooks"].setdefault("Stop", []).append(foreign)
    settings.write_text(json.dumps(data))
    removed = runner.invoke(
        app, ["setup", "--client", "claude", "--scope", "project", "--uninstall"]
    )
    assert removed.exit_code == 0, removed.output
    assert agent_hooks.claude_hooks_installed() == []
    assert json.loads(settings.read_text())["hooks"]["Stop"] == [foreign]


def test_setup_uninstall_removes_the_codex_context_block(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    _setup_assets(monkeypatch)
    agents = agent_hooks.codex_agents_path()
    agents.parent.mkdir(parents=True, exist_ok=True)
    agents.write_text("# My own Codex notes\n")
    agent_hooks.write_agents_block("Beakr context")
    removed = CliRunner().invoke(app, ["setup", "--client", "codex", "--uninstall", "--no-mcp"])
    assert removed.exit_code == 0, removed.output
    assert not agent_hooks.agents_block_installed()
    assert "# My own Codex notes" in agents.read_text()
