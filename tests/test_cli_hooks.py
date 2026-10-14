"""``beakr hooks``: idempotent, never touches anyone else's hooks or settings."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import agent_hooks
from beakr_cli.commands.hooks import hooks_status, install_hooks_during_setup
from beakr_cli.main import app

API = "https://api.test"

FOREIGN_HOOK = {
    "matcher": "Bash",
    "hooks": [{"type": "command", "command": "/usr/local/bin/audit-bash", "timeout": 5}],
}


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setattr("beakr_cli.agent_hooks._beakr_command", lambda: "/opt/bin/beakr")


def _settings_path(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def _commands(settings: dict, event: str) -> list[str]:
    return [
        hook["command"] for group in settings["hooks"].get(event, []) for hook in group["hooks"]
    ]


def test_install_twice_is_idempotent_and_preserves_foreign_settings(isolated_home: Path) -> None:
    path = _settings_path(isolated_home)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"model": "opus", "hooks": {"PreToolUse": [FOREIGN_HOOK], "Stop": [FOREIGN_HOOK]}}
        )
    )

    for _ in range(2):
        result = CliRunner().invoke(app, ["hooks", "install"])
        assert result.exit_code == 0, result.output

    settings = json.loads(path.read_text())
    assert settings["model"] == "opus"
    assert settings["hooks"]["PreToolUse"] == [FOREIGN_HOOK]
    assert _commands(settings, "SessionStart") == [
        "/opt/bin/beakr context --format claude_md --project auto --hook"
    ]
    assert _commands(settings, "Stop") == [
        "/usr/local/bin/audit-bash",
        "/opt/bin/beakr compound --hook --client claude-code --background",
    ]
    assert _commands(settings, "SessionEnd") == [
        "/opt/bin/beakr compound --hook --client claude-code --background --force"
    ]
    assert settings["hooks"]["SessionStart"][0]["matcher"] == ""


def test_reinstall_replaces_an_entry_from_an_old_binary_path(
    isolated_home: Path, monkeypatch
) -> None:
    CliRunner().invoke(app, ["hooks", "install"])
    monkeypatch.setattr("beakr_cli.agent_hooks._beakr_command", lambda: "/new/bin/beakr")

    CliRunner().invoke(app, ["hooks", "install"])

    settings = json.loads(_settings_path(isolated_home).read_text())
    assert _commands(settings, "SessionStart") == [
        "/new/bin/beakr context --format claude_md --project auto --hook"
    ]


def test_uninstall_removes_only_beakr_hooks_and_prunes_empty(isolated_home: Path) -> None:
    path = _settings_path(isolated_home)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hooks": {"Stop": [FOREIGN_HOOK]}, "theme": "dark"}))
    CliRunner().invoke(app, ["hooks", "install"])

    result = CliRunner().invoke(app, ["hooks", "uninstall", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["removed"] is True
    assert json.loads(path.read_text()) == {"hooks": {"Stop": [FOREIGN_HOOK]}, "theme": "dark"}


def test_uninstall_drops_hooks_key_when_only_beakr_was_there(isolated_home: Path) -> None:
    CliRunner().invoke(app, ["hooks", "install"])
    CliRunner().invoke(app, ["hooks", "uninstall"])
    assert json.loads(_settings_path(isolated_home).read_text()) == {}
    second = CliRunner().invoke(app, ["hooks", "uninstall", "--json"])
    assert json.loads(second.stdout)[0]["removed"] is False


def test_invalid_settings_json_is_refused_not_clobbered(isolated_home: Path) -> None:
    path = _settings_path(isolated_home)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")

    result = CliRunner().invoke(app, ["hooks", "install"])

    assert result.exit_code == 1
    assert path.read_text() == "{ not json"


def test_status_reports_installed_events() -> None:
    assert hooks_status()["claude"]["installed"] is False
    CliRunner().invoke(app, ["hooks", "install"])
    status = hooks_status()
    assert status["claude"]["complete"] is True
    assert set(status["claude"]["events"]) == {"SessionStart", "Stop", "SessionEnd"}
    result = CliRunner().invoke(app, ["hooks", "status", "--json"])
    assert json.loads(result.stdout)["claude"]["installed"] is True


@respx.mock
def test_codex_agents_block_is_appended_replaced_and_removed(isolated_home: Path) -> None:
    agents = isolated_home / ".codex" / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text("# My rules\n\nAlways run tests.\n")
    route = respx.post(f"{API}/v1/context").mock(
        side_effect=[
            httpx.Response(200, json={"content": "first context"}),
            httpx.Response(200, json={"content": "second context"}),
        ]
    )

    CliRunner().invoke(app, ["hooks", "install", "--client", "codex"])
    CliRunner().invoke(app, ["hooks", "install", "--client", "codex"])

    text = agents.read_text()
    assert json.loads(route.calls[0].request.content) == {"format": "agents_md"}
    assert text.startswith("# My rules\n\nAlways run tests.\n")
    assert text.count(agent_hooks.AGENTS_START) == 1
    assert "second context" in text and "first context" not in text

    CliRunner().invoke(app, ["hooks", "uninstall", "--client", "codex"])
    assert agents.read_text() == "# My rules\n\nAlways run tests.\n"


def test_setup_flag_false_skips_and_none_skips_when_not_interactive(
    isolated_home: Path, monkeypatch
) -> None:
    monkeypatch.setattr("beakr_cli.commands.hooks.is_interactive", lambda: False)
    install_hooks_during_setup(["claude"], False)
    install_hooks_during_setup(["claude"], None)
    assert not _settings_path(isolated_home).exists()


def test_setup_flag_true_installs(isolated_home: Path) -> None:
    install_hooks_during_setup(["claude"], True)
    assert agent_hooks.claude_hooks_installed() == ["SessionStart", "Stop", "SessionEnd"]


def test_setup_command_passes_no_hooks(isolated_home: Path, monkeypatch) -> None:
    (isolated_home / ".claude").mkdir()
    calls: list[tuple] = []
    monkeypatch.setattr(
        "beakr_cli.commands.hooks.install_hooks_during_setup",
        lambda targets, flag: calls.append((targets, flag)),
    )
    result = CliRunner().invoke(
        app, ["setup", "--client", "claude", "--no-auth", "--no-skills", "--no-mcp", "--no-hooks"]
    )
    assert result.exit_code == 0, result.output
    assert calls == [(["claude"], False)]
