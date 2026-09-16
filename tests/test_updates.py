"""Release awareness: the update check, install detection, and upgrade flow.

These exist because nothing used to tell a user they were behind. v0.2.1 on PyPI
could not start its MCP server once ``mcp`` 2.x shipped, and every copy of it
stayed broken silently because neither surface ever looked for a newer release.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import config, updates
from beakr_cli.main import app


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point ~/.beakr at a temp dir so the cache never touches the real one."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".beakr")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / ".beakr" / "config.json")
    monkeypatch.delenv(updates.DISABLE_ENV, raising=False)
    monkeypatch.setattr(updates, "__version__", "0.3.0")
    return tmp_path


def _pypi(*versions: str, yanked: tuple[str, ...] = ()) -> dict:
    return {
        "releases": {
            v: [{"filename": f"beakr_cli-{v}.whl", "yanked": v in yanked}] for v in versions
        }
    }


def _write_cache(latest: str, age: timedelta) -> None:
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    checked_at = datetime.now(timezone.utc) - age
    (config.CONFIG_DIR / "update_check.json").write_text(
        json.dumps({"latest": latest, "checked_at": checked_at.isoformat()})
    )


# ---------------------------------------------------------------------------
# Version check
# ---------------------------------------------------------------------------


@respx.mock
def test_latest_version_skips_prereleases_and_fully_yanked_releases() -> None:
    respx.get(updates.PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json=_pypi("0.2.1", "0.3.0", "0.4.0", "0.5.0rc1",
                                                    yanked=("0.4.0",)))
    )
    assert updates.fetch_latest_version(timeout=1) == "0.3.0"


@respx.mock
def test_newer_release_is_reported_and_cached() -> None:
    route = respx.get(updates.PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json=_pypi("0.3.0", "0.3.1"))
    )
    status = updates.get_update_status()
    assert status.latest == "0.3.1"
    assert status.update_available

    # A second check within the cache window must not hit the network again.
    assert updates.get_update_status().latest == "0.3.1"
    assert route.call_count == 1


@respx.mock
def test_fresh_cache_is_used_without_network() -> None:
    route = respx.get(updates.PYPI_JSON_URL).mock(return_value=httpx.Response(500))
    _write_cache("0.9.0", age=timedelta(hours=1))
    status = updates.get_update_status()
    assert status.latest == "0.9.0"
    assert route.call_count == 0


@respx.mock
def test_network_failure_falls_back_to_stale_cache() -> None:
    respx.get(updates.PYPI_JSON_URL).mock(side_effect=httpx.ConnectError("offline"))
    _write_cache("0.4.0", age=timedelta(days=3))
    status = updates.get_update_status()
    assert status.latest == "0.4.0"
    assert status.update_available


@respx.mock
def test_disabled_check_never_touches_network(monkeypatch) -> None:
    monkeypatch.setenv(updates.DISABLE_ENV, "1")
    route = respx.get(updates.PYPI_JSON_URL).mock(return_value=httpx.Response(200, json={}))
    status = updates.get_update_status(max_age=timedelta(0))
    assert status.latest is None
    assert not status.update_available
    assert route.call_count == 0


@respx.mock
def test_cached_latest_older_than_running_version_is_refetched() -> None:
    """Right after upgrading, a cache written by the old version must not be shown."""
    route = respx.get(updates.PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json=_pypi("0.2.1", "0.3.0"))
    )
    _write_cache("0.2.1", age=timedelta(minutes=5))
    status = updates.get_update_status()
    assert status.latest == "0.3.0"
    assert route.call_count == 1


@respx.mock
def test_stale_cdn_listing_older_than_running_version_is_not_cached() -> None:
    """PyPI's CDN can list the previous release for ~15 minutes after publishing.

    That answer was cached for a day and shown as "latest release: 0.2.1" on 0.3.0.
    """
    respx.get(updates.PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json=_pypi("0.2.0", "0.2.1"))
    )
    status = updates.get_update_status()
    assert status.latest is None
    assert not status.update_available
    assert not (config.CONFIG_DIR / "update_check.json").exists()


def test_running_ahead_of_pypi_is_not_an_update() -> None:
    assert not updates.UpdateStatus("0.3.0", "0.2.1", None).update_available


# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------

#: Receipts exactly as uv 0.11 writes them. The entrypoints block matters: its
#: ``install-path = ...`` made every PyPI install look like a local checkout when
#: detection searched the whole file for ``path =``, so `beakr update` refused to
#: upgrade any uv tool install in 0.3.0 and 0.3.1.
_ENTRYPOINTS = """entrypoints = [
    { name = "beakr", install-path = "/Users/u/.local/bin/beakr", from = "beakr-cli" },
]
"""
PYPI_RECEIPT = '[tool]\nrequirements = [{ name = "beakr-cli" }]\n' + _ENTRYPOINTS
PINNED_RECEIPT = (
    '[tool]\nrequirements = [{ name = "beakr-cli", specifier = "==0.3.0" }]\n' + _ENTRYPOINTS
)
RANGE_RECEIPT = (
    '[tool]\nrequirements = [{ name = "beakr-cli", specifier = ">=0.3" }]\n' + _ENTRYPOINTS
)
SOURCE_RECEIPT = (
    '[tool]\nrequirements = [{ name = "beakr-cli", directory = "/src/beakr-cli" }]\n'
    'constraints = [{ name = "mcp", specifier = "<2" }]\n' + _ENTRYPOINTS
)


def _uv_tool_env(tmp_path, monkeypatch, receipt: str) -> Path:
    tools = tmp_path / "uv" / "tools"
    env = tools / "beakr-cli"
    env.mkdir(parents=True)
    (env / "uv-receipt.toml").write_text(receipt)
    monkeypatch.setenv("UV_TOOL_DIR", str(tools))
    return env


@pytest.mark.parametrize("receipt", [PYPI_RECEIPT, RANGE_RECEIPT])
def test_detects_pypi_uv_tool_install(receipt, tmp_path, monkeypatch) -> None:
    info = updates.detect_install(_uv_tool_env(tmp_path, monkeypatch, receipt))
    assert info.method is updates.InstallMethod.uv_tool
    # --reinstall-package implies a refresh of the cached index; without one an
    # upgrade shortly after a release resolved the old version and still exited 0.
    assert info.upgrade_command == [
        "uv", "tool", "upgrade", "--reinstall-package", "beakr-cli", "beakr-cli",
    ]


def test_version_pinned_uv_tool_is_reported_not_upgraded(tmp_path, monkeypatch) -> None:
    """`uv tool upgrade` keeps a `==X` pin, so running it would upgrade nothing."""
    info = updates.detect_install(_uv_tool_env(tmp_path, monkeypatch, PINNED_RECEIPT))
    assert info.method is updates.InstallMethod.uv_tool_pinned
    assert info.upgrade_command is None
    assert "uv tool install --force beakr-cli" in info.instructions


def test_local_checkout_uv_tool_is_never_upgraded_from_pypi(tmp_path, monkeypatch) -> None:
    """A developer's local build must not be silently replaced by the release."""
    info = updates.detect_install(_uv_tool_env(tmp_path, monkeypatch, SOURCE_RECEIPT))
    assert info.method is updates.InstallMethod.uv_tool_source
    assert info.upgrade_command is None


@pytest.mark.parametrize(
    ("prefix", "method", "can_upgrade"),
    [
        ("/home/u/.local/pipx/venvs/beakr-cli", updates.InstallMethod.pipx, True),
        ("/home/u/.cache/uv/archive-v0/abc123", updates.InstallMethod.uvx, False),
        ("/usr/local", updates.InstallMethod.other, False),
    ],
)
def test_detects_other_install_methods(prefix, method, can_upgrade, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "no-tools-here"))
    info = updates.detect_install(Path(prefix))
    assert info.method is method
    assert (info.upgrade_command is not None) is can_upgrade


def test_detects_pipx_with_a_custom_pipx_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "no-tools-here"))
    monkeypatch.setenv("PIPX_HOME", str(tmp_path / "custom-home"))
    env = tmp_path / "custom-home" / "venvs" / "beakr-cli"
    env.mkdir(parents=True)
    info = updates.detect_install(env)
    assert info.method is updates.InstallMethod.pipx
    assert info.upgrade_command == ["pipx", "upgrade", "--pip-args=--no-cache-dir", "beakr-cli"]


def test_upgrade_without_a_safe_command_only_returns_instructions() -> None:
    info = updates.InstallInfo(updates.InstallMethod.uvx, None, "use uvx --refresh")
    result = updates.run_upgrade(info)
    assert not result.ok
    assert result.message == "use uvx --refresh"


def test_upgrade_refreshes_skills_through_the_new_binary(monkeypatch) -> None:
    """The refresh must run the upgraded `beakr`, not this process's stale code."""
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return Done()

    monkeypatch.setattr(updates.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    info = updates.InstallInfo(updates.InstallMethod.uv_tool, ["uv", "tool", "upgrade", "x"], "")
    result = updates.run_upgrade(info)
    assert result.ok
    assert calls == [["uv", "tool", "upgrade", "x"], ["/bin/beakr", "install", "--refresh"]]


def test_upgrade_that_did_not_move_the_version_is_a_failure(monkeypatch) -> None:
    """A package manager exiting 0 is not an upgrade; skills must not be refreshed."""
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return Done()

    monkeypatch.setattr(updates.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    monkeypatch.setattr(updates, "installed_version_on_path", lambda: "0.3.0")
    info = updates.InstallInfo(updates.InstallMethod.uv_tool, ["uv", "tool", "upgrade", "x"], "")
    result = updates.run_upgrade(info, expected_version="0.3.1")
    assert not result.ok
    assert "still 0.3.0" in result.message
    assert calls == [["uv", "tool", "upgrade", "x"]]


# ---------------------------------------------------------------------------
# Skill refresh
# ---------------------------------------------------------------------------


def test_refresh_only_touches_locations_that_already_have_the_skill(tmp_path, monkeypatch) -> None:
    from beakr_cli.commands import install

    claude_home = tmp_path / "claude"
    codex_home = tmp_path / "codex"
    stale_skill = claude_home / "skills" / "beakr"
    stale_skill.mkdir(parents=True)
    (stale_skill / "SKILL.md").write_text("stale kb_search instructions")
    codex_home.mkdir()  # Codex installed, but Beakr never wired into it.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.chdir(tmp_path)

    assets = tmp_path / "assets"
    (assets / "skills" / "beakr").mkdir(parents=True)
    (assets / "skills" / "beakr" / "SKILL.md").write_text("new knowledge_base instructions")
    (assets / "commands").mkdir()
    (assets / "commands" / "kb-search.md").write_text("new command")
    monkeypatch.setattr(install, "_assets_root", lambda: assets)

    assert install.refresh_installed_assets() == 1
    assert (stale_skill / "SKILL.md").read_text() == "new knowledge_base instructions"
    assert (claude_home / "commands" / "kb-search.md").read_text() == "new command"
    assert not (codex_home / "skills").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_update_check_reports_how_to_upgrade(monkeypatch) -> None:
    monkeypatch.setattr(
        updates, "get_update_status", lambda **_: updates.UpdateStatus("0.3.0", "0.3.1", None)
    )
    monkeypatch.setattr(
        updates,
        "detect_install",
        lambda: updates.InstallInfo(updates.InstallMethod.pipx, ["pipx"], "Run pipx upgrade."),
    )
    result = CliRunner().invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "Latest: 0.3.1" in result.output
    assert "Run pipx upgrade." in result.output


def test_update_is_a_no_op_when_current(monkeypatch) -> None:
    monkeypatch.setattr(
        updates, "get_update_status", lambda **_: updates.UpdateStatus("0.3.0", "0.3.0", None)
    )

    def must_not_upgrade(_info):
        raise AssertionError("upgrade ran although no update was available")

    monkeypatch.setattr(updates, "run_upgrade", must_not_upgrade)
    result = CliRunner().invoke(app, ["update"])
    assert result.exit_code == 0
    assert "No update needed" in result.output


def test_update_refuses_installs_it_cannot_upgrade_safely(monkeypatch) -> None:
    monkeypatch.setattr(
        updates, "get_update_status", lambda **_: updates.UpdateStatus("0.3.0", "0.3.1", None)
    )
    monkeypatch.setattr(
        updates,
        "detect_install",
        lambda: updates.InstallInfo(updates.InstallMethod.uvx, None, "Use uvx --refresh."),
    )
    result = CliRunner().invoke(app, ["update"])
    assert result.exit_code == 1
    assert "Use uvx --refresh." in result.output


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def test_mcp_instructions_announce_a_cached_newer_release(monkeypatch) -> None:
    from beakr_cli import mcp_server

    monkeypatch.setattr(
        mcp_server,
        "get_update_status",
        lambda **_: updates.UpdateStatus("0.3.0", "0.4.0", None),
    )
    notice = mcp_server._update_notice()
    assert "0.4.0" in notice
    assert "update_beakr" in notice

    monkeypatch.setattr(
        mcp_server,
        "get_update_status",
        lambda **_: updates.UpdateStatus("0.3.0", "0.3.0", None),
    )
    assert mcp_server._update_notice() == ""


async def test_update_tool_does_nothing_when_current(monkeypatch) -> None:
    from beakr_cli import mcp_server

    monkeypatch.setattr(
        mcp_server,
        "get_update_status",
        lambda **_: updates.UpdateStatus("0.3.0", "0.3.0", None),
    )

    def must_not_upgrade(_info):
        raise AssertionError("upgrade ran although no update was available")

    monkeypatch.setattr(mcp_server, "run_upgrade", must_not_upgrade)
    assert "No update needed" in await mcp_server.update_beakr()
