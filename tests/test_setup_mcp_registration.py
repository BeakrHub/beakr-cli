"""``beakr setup`` wires Claude Code and Codex to this install, its API and its key.

Regressions (found on a fresh install against sandbox, 2026-10-25):

- The MCP registration and the session hooks used ``shutil.which("beakr")``, so
  on a machine with two installs (a self-hosted box client ahead of the
  ``uv tool`` one on PATH) setup registered the *other* binary.
- The registration carried no settings (``env: {}``): a GUI-launched Claude Code
  or Codex has no shell environment, so a sandbox or self-hosted
  ``BEAKR_BASE_URL`` and a non-default ``BEAKR_HOME`` were lost and the server
  talked to production.
- With ``$BEAKR_API_KEY`` set and nothing saved, setup reported auth as done
  but persisted nothing, so the server it registered started signed out.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
import respx
from beakr_sdk.credentials import TokenStore

from beakr_cli import agent_hooks
from beakr_cli.commands import install

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    tomllib = pytest.importorskip("tomli")

SANDBOX = "https://api-sandbox.thebeakr.com"


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def other_beakr_first_on_path(tmp_path: Path, monkeypatch) -> Path:
    """A different install's ``beakr`` that PATH lookup would find first."""
    other = _executable(tmp_path / "box-client" / "bin" / "beakr")
    monkeypatch.setenv("PATH", f"{other.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    return other


def test_running_console_script_is_registered_not_the_first_on_path(
    tmp_path: Path, monkeypatch, other_beakr_first_on_path: Path
) -> None:
    running = _executable(tmp_path / "uv-tools" / "bin" / "beakr")
    monkeypatch.setattr(sys, "argv", [str(running), "setup"])

    assert install._beakr_command() == str(running)


def test_script_beside_the_interpreter_is_used_under_python_m(
    tmp_path: Path, monkeypatch, other_beakr_first_on_path: Path
) -> None:
    beside = _executable(tmp_path / "venv" / "bin" / "beakr")
    monkeypatch.setattr(sys, "argv", ["/somewhere/beakr_cli/__main__.py", "setup"])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "bin" / "python"))

    assert install._beakr_command() == str(beside)


def test_path_lookup_is_only_the_last_resort(
    tmp_path: Path, monkeypatch, other_beakr_first_on_path: Path
) -> None:
    monkeypatch.setattr(sys, "argv", ["/nowhere/pytest"])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-venv" / "python"))

    assert install._beakr_command() == str(other_beakr_first_on_path)


def test_defaults_add_no_env(isolated_home: Path) -> None:
    assert install._mcp_env() == {}


def test_non_default_api_and_home_are_carried(tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "profiles" / "sandbox"
    monkeypatch.setenv("BEAKR_HOME", str(profile))
    monkeypatch.setenv("BEAKR_BASE_URL", f"{SANDBOX}/")

    assert install._mcp_env() == {"BEAKR_BASE_URL": SANDBOX, "BEAKR_HOME": str(profile)}


def test_saved_api_url_is_carried(isolated_home: Path) -> None:
    from beakr_cli import config

    config.set_key("api_url", SANDBOX)

    assert install._mcp_env() == {"BEAKR_BASE_URL": SANDBOX}


def test_claude_cli_registration_passes_the_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_BASE_URL", SANDBOX)
    monkeypatch.setattr(install, "_beakr_command", lambda: "/opt/tools/beakr")
    monkeypatch.setattr(install, "_claude_cli", lambda: "/usr/local/bin/claude")
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(install.subprocess, "run", lambda args, **_: calls.append(args) or Done())

    install._register_claude_mcp(force=False, scope=install.Scope.user)

    (args,) = calls
    assert args[args.index("--") :] == ["--", "/opt/tools/beakr", "mcp"]
    assert ["-e", f"BEAKR_BASE_URL={SANDBOX}"] == args[args.index("-e") : args.index("--")]
    assert not any("BEAKR_API_KEY" in arg for arg in args)


def test_claude_json_fallback_writes_the_env_but_never_the_key(
    isolated_home: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("BEAKR_BASE_URL", SANDBOX)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_secret")
    monkeypatch.setattr(install, "_beakr_command", lambda: "/opt/tools/beakr")
    monkeypatch.setattr(install, "_claude_cli", lambda: None)

    install._register_claude_mcp(force=False, scope=install.Scope.user)

    text = (isolated_home / ".claude.json").read_text()
    assert json.loads(text)["mcpServers"]["beakr"] == {
        "command": "/opt/tools/beakr",
        "args": ["mcp"],
        "env": {"BEAKR_BASE_URL": SANDBOX},
    }
    assert "bk_live_secret" not in text


def test_project_mcp_json_writes_the_env(isolated_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_BASE_URL", SANDBOX)
    monkeypatch.setattr(install, "_beakr_command", lambda: "/opt/tools/beakr")
    monkeypatch.setattr(install, "_claude_cli", lambda: None)

    install._register_claude_mcp(force=False, scope=install.Scope.project)

    entry = json.loads((Path.cwd() / ".mcp.json").read_text())["mcpServers"]["beakr"]
    assert entry["env"] == {"BEAKR_BASE_URL": SANDBOX}


def test_codex_config_fallback_writes_the_env(tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "profile"
    monkeypatch.setenv("BEAKR_HOME", str(profile))
    monkeypatch.setenv("BEAKR_BASE_URL", SANDBOX)
    monkeypatch.setattr(install, "_beakr_command", lambda: "/opt/my tools/beakr")
    monkeypatch.setattr(install, "_codex_cli", lambda: None)

    install._register_codex_mcp(force=False, scope=install.Scope.user)

    parsed = tomllib.loads(install._codex_config().read_text())
    assert parsed["mcp_servers"]["beakr"] == {
        "command": "/opt/my tools/beakr",
        "args": ["mcp"],
        "env": {"BEAKR_BASE_URL": SANDBOX, "BEAKR_HOME": str(profile)},
    }


def test_codex_cli_registration_passes_the_env(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_BASE_URL", SANDBOX)
    monkeypatch.setattr(install, "_beakr_command", lambda: "/opt/tools/beakr")
    monkeypatch.setattr(install, "_codex_cli", lambda: "/usr/local/bin/codex")
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(install.subprocess, "run", lambda args, **_: calls.append(args) or Done())

    install._register_codex_mcp(force=False, scope=install.Scope.user)

    (args,) = calls
    assert args == [
        "/usr/local/bin/codex",
        "mcp",
        "add",
        "beakr",
        "--env",
        f"BEAKR_BASE_URL={SANDBOX}",
        "--",
        "/opt/tools/beakr",
        "mcp",
    ]


@pytest.mark.skipif(os.name == "nt", reason="hook env prefix is POSIX-only")
def test_hooks_carry_the_env_and_stay_recognisable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_BASE_URL", SANDBOX)
    monkeypatch.setattr(agent_hooks, "_beakr_command", lambda: "/opt/tools/beakr")

    command = agent_hooks._beakr_invocation(agent_hooks.CONTEXT_ARGS)

    assert command == f"BEAKR_BASE_URL={SANDBOX} /opt/tools/beakr {agent_hooks.CONTEXT_ARGS}"
    assert agent_hooks.is_beakr_hook_command(command)


@respx.mock
def test_env_key_is_saved_when_nothing_is_stored(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setenv("BEAKR_BASE_URL", SANDBOX)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_from_env")
    respx.get(f"{SANDBOX}/v1/me").respond(
        json={"user": {"display_name": "Ada"}, "personal_org": {"name": "Lab"}}
    )

    install._maybe_auth()

    stored = TokenStore().load(SANDBOX)
    assert stored is not None and stored.is_api_key
    assert stored.access_token == "bk_live_from_env"


@respx.mock
def test_env_key_does_not_replace_a_stored_credential(monkeypatch) -> None:
    from beakr_cli import config

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setenv("BEAKR_BASE_URL", SANDBOX)
    config.save_api_key("bk_live_saved", SANDBOX)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_from_env")
    respx.get(f"{SANDBOX}/v1/me").respond(
        json={"user": {"display_name": "Ada"}, "personal_org": {"name": "Lab"}}
    )

    install._maybe_auth()

    assert TokenStore().load(SANDBOX).access_token == "bk_live_saved"
