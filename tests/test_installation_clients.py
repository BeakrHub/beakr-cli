"""A self-hosted build against the clients its installation's release ships.

The installation serves ``/clients/manifest.json`` (beakr-engine client_index.py,
from the web image). A self-hosted CLI that differs from the beakr-cli it names
prints one upgrade line: at startup at most once a day, in ``beakr doctor``,
``beakr version`` and the stdio MCP server's startup log. An installation that
serves no manifest, or cannot be reached, is silence, and nothing waits on it.
"""

from __future__ import annotations

import io
import json
import socket
import sys
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import respx
from rich.console import Console
from typer.testing import CliRunner

from beakr_cli import installation_clients, updates
from beakr_cli.main import app

HOST = "https://beakr.example.com"
RUNNING = "0.7.0+selfhosted.g09fb362d9639"
SHIPPED = "0.8.0+selfhosted.gaaaaaaaaaaaa"
SDK = "0.5.0+selfhosted.gbbbbbbbbbbbb"


def manifest(cli: str = SHIPPED, *, release: str = "1.0.7", schema: int = 1) -> dict:
    return {
        "schema_version": schema,
        "release": release,
        "python_index": "/pypi/simple/",
        "clients": {
            "beakr-cli": {"kind": "python", "version": cli, "files": [], "install": "x"},
            "beakr-sdk": {"kind": "python", "version": SDK, "files": [], "install": "x"},
            "@beakr/sdk": {"kind": "npm", "version": "0.5.0-selfhosted.gc", "files": []},
        },
    }


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.setattr(updates, "__version__", RUNNING)
    monkeypatch.setenv("BEAKR_BASE_URL", HOST)
    # Not a uv tool install unless a test says so.
    monkeypatch.setattr(installation_clients, "_uses_uv_tool", lambda: False)


def _expected_line(cli: str = SHIPPED) -> str:
    return (
        f"This installation (release 1.0.7) ships beakr-cli {cli}; you have {RUNNING}. "
        f'Upgrade: pip install -U --extra-index-url {HOST}/pypi/simple "beakr-cli=={cli}" '
        f'"beakr-sdk=={SDK}"'
    )


def _run_notice(budget: float = 1.5) -> list[str]:
    """One command's startup notice: started before it, finished after it."""
    printed: list[str] = []
    notice = installation_clients.start_notice()
    if notice is not None:
        notice.budget = budget
        notice.finish(printed.append)
    return printed


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


def test_parse_reads_the_release_and_both_python_versions() -> None:
    assert installation_clients.parse_manifest(manifest()) == ("1.0.7", SHIPPED, SDK)


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "text",
        manifest(schema=2),
        {**manifest(), "release": ""},
        {**manifest(), "clients": {"beakr-sdk": {"version": SDK}}},
        {**manifest(), "clients": {"beakr-cli": {"version": 7}}},
    ],
)
def test_parse_refuses_anything_else(data) -> None:
    assert installation_clients.parse_manifest(data) is None


# ---------------------------------------------------------------------------
# The startup notice
# ---------------------------------------------------------------------------


@respx.mock
def test_a_mismatch_prints_the_upgrade_line_once_a_day(self_hosted) -> None:
    route = respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())

    first = _run_notice()
    second = _run_notice()

    assert first == [_expected_line()]
    # Once a day: the second command reads the cache and stays quiet.
    assert second == []
    assert route.call_count == 1


@respx.mock
def test_the_line_comes_back_after_a_day_and_for_a_new_release(self_hosted, monkeypatch) -> None:
    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())
    assert _run_notice() == [_expected_line()]

    later = installation_clients._now() + timedelta(hours=25)
    monkeypatch.setattr(installation_clients, "_now", lambda: later)
    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest("0.9.0+selfhosted.gc"))
    assert _run_notice() == [_expected_line("0.9.0+selfhosted.gc")]


@respx.mock
def test_a_match_prints_nothing(self_hosted) -> None:
    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest(RUNNING))
    assert _run_notice() == []


@respx.mock
def test_a_uv_tool_install_gets_the_uv_command(self_hosted, monkeypatch) -> None:
    monkeypatch.setattr(installation_clients, "_uses_uv_tool", lambda: True)
    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())

    (line,) = _run_notice()

    assert line.endswith(
        f"Upgrade: uv tool install --force --extra-index-url {HOST}/pypi/simple "
        f'"beakr-cli=={SHIPPED}" --with "beakr-sdk=={SDK}"'
    )


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404, text="Not found"),
        httpx.Response(200, text="<html>sign in</html>"),
        httpx.Response(200, json=manifest(schema=2)),
        httpx.Response(302, headers={"location": "/auth"}),
    ],
)
@respx.mock
def test_no_manifest_is_silence(self_hosted, answer) -> None:
    respx.get(f"{HOST}/clients/manifest.json").mock(return_value=answer)
    assert _run_notice() == []


@respx.mock
def test_an_unreachable_installation_is_silent_and_not_asked_again_for_an_hour(
    self_hosted, monkeypatch
) -> None:
    route = respx.get(f"{HOST}/clients/manifest.json").mock(
        side_effect=httpx.ConnectError("refused")
    )

    assert _run_notice() == []
    assert _run_notice() == []
    assert route.call_count == 1
    assert not installation_clients.needs_refresh(HOST)

    later = installation_clients._now() + timedelta(minutes=61)
    monkeypatch.setattr(installation_clients, "_now", lambda: later)
    assert installation_clients.needs_refresh(HOST)


def _hanging_server() -> tuple[str, socket.socket]:
    """A local server that accepts connections and never answers."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            held.append(connection)

    threading.Thread(target=accept, daemon=True).start()
    return f"http://127.0.0.1:{server.getsockname()[1]}", server


def test_a_hanging_installation_never_holds_the_command_up(self_hosted, monkeypatch) -> None:
    url, server = _hanging_server()
    monkeypatch.setenv("BEAKR_BASE_URL", url)
    try:
        started = time.monotonic()
        notice = installation_clients.start_notice()
        assert notice is not None and notice.thread is not None
        # Starting it returns at once: the request runs in the background.
        assert time.monotonic() - started < 0.2
        notice.budget = 0.3
        printed: list[str] = []
        notice.finish(printed.append)
        assert time.monotonic() - started < 1.0
        assert printed == []
        # The attempt was recorded before the request, so the next command does
        # not ask the silent host again.
        assert not installation_clients.needs_refresh(url)
        assert installation_clients.start_notice().thread is None
    finally:
        server.close()


@respx.mock
def test_a_public_build_never_asks(monkeypatch) -> None:
    monkeypatch.setattr(updates, "__version__", "0.7.0")
    monkeypatch.setenv("BEAKR_BASE_URL", HOST)
    route = respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())

    assert installation_clients.start_notice() is None
    assert installation_clients.server_log_line() is None
    assert route.call_count == 0


@respx.mock
def test_the_cli_startup_prints_the_line_after_the_command(self_hosted, monkeypatch) -> None:
    from beakr_cli import main, output

    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stderr", _Tty())
    monkeypatch.setattr(output, "err_console", Console(file=captured, width=400))
    closers: list = []
    ctx = SimpleNamespace(invoked_subcommand="ask", call_on_close=closers.append)

    main._main(ctx, show_version=False)
    # Nothing before the command runs; the line once its context closes.
    assert captured.getvalue() == ""
    for close in closers:
        close()
    assert captured.getvalue().strip() == _expected_line()

    # Not for the MCP server, which owns stdout, nor for version/update.
    closers.clear()
    main._main(SimpleNamespace(invoked_subcommand="mcp", call_on_close=closers.append), False)
    assert closers == []


# ---------------------------------------------------------------------------
# doctor, version and the MCP server
# ---------------------------------------------------------------------------


@respx.mock
def test_doctor_names_the_shipped_version(self_hosted) -> None:
    from beakr_cli.commands.doctor import check_installation_clients

    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())
    warn = check_installation_clients(HOST)
    assert warn["status"] == "warn" and warn["detail"] == _expected_line()
    assert warn["release"] == "1.0.7" and warn["expected"] == SHIPPED


@respx.mock
def test_doctor_on_a_match_and_without_a_manifest(self_hosted) -> None:
    from beakr_cli.commands.doctor import check_installation_clients

    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest(RUNNING))
    assert check_installation_clients(HOST)["status"] == "ok"

    respx.get("https://old.example.com/clients/manifest.json").respond(404)
    missing = check_installation_clients("https://old.example.com")
    assert missing["status"] == "ok" and "serves no client manifest" in missing["detail"]


@respx.mock
def test_doctor_lists_the_clients_row_for_a_self_hosted_build(self_hosted, monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_testkey123")
    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())
    respx.get(f"{HOST}/v1/public/openapi.json").respond(404)
    respx.get(f"{HOST}/v1/me").respond(json={"user": {"display_name": "Ada"}})
    respx.get(f"{HOST}/v1/me/api-keys").respond(json=[])

    result = CliRunner().invoke(app, ["doctor", "--json"])

    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["clients"]["status"] == "warn"
    assert checks["clients"]["detail"] == _expected_line()


@respx.mock
def test_version_names_what_the_installation_ships(self_hosted) -> None:
    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())
    respx.get(url__regex=rf"{HOST}/v1/.*").respond(404)

    text = CliRunner().invoke(app, ["version"]).output
    report = json.loads(CliRunner().invoke(app, ["version", "--json"]).output)

    assert _expected_line() in text
    assert f"{HOST}/pypi/simple" in text
    assert report["installation"] == {
        "release": "1.0.7",
        "beakr_cli": SHIPPED,
        "beakr_sdk": SDK,
        "matches": False,
    }


@respx.mock
def test_version_on_a_match_says_this_one(self_hosted) -> None:
    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest(RUNNING))
    respx.get(url__regex=rf"{HOST}/v1/.*").respond(404)

    text = CliRunner().invoke(app, ["version"]).output

    assert f"ships beakr-cli {RUNNING}: this one." in text


@respx.mock
def test_the_mcp_server_logs_the_line_to_stderr_never_stdout(self_hosted, capsys) -> None:
    from beakr_cli import mcp_server

    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())

    mcp_server._refresh_update_cache()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert _expected_line() in captured.err.splitlines()


@respx.mock
def test_the_mcp_server_logs_every_start_and_nothing_on_a_match(self_hosted, capsys) -> None:
    from beakr_cli import mcp_server

    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())
    mcp_server._refresh_update_cache()
    mcp_server._refresh_update_cache()
    assert capsys.readouterr().err.count("This installation") == 2

    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest(RUNNING))
    installation_clients._write_all({})
    mcp_server._refresh_update_cache()
    assert "This installation" not in capsys.readouterr().err


@respx.mock
async def test_the_mcp_version_tool_names_the_shipped_version(self_hosted) -> None:
    from beakr_cli import mcp_server

    respx.get(f"{HOST}/clients/manifest.json").respond(json=manifest())
    respx.get(url__regex=rf"{HOST}/v1/.*").respond(404)

    text = await mcp_server.beakr_version()

    assert _expected_line() in text


@respx.mock
def test_doctor_asks_again_within_the_hour_after_a_failure(self_hosted) -> None:
    from beakr_cli.commands.doctor import check_installation_clients

    route = respx.get(f"{HOST}/clients/manifest.json")
    route.side_effect = httpx.ConnectError("refused")
    assert _run_notice() == []
    route.side_effect = None
    route.return_value = httpx.Response(200, json=manifest())

    # The startup check waits out the hour; a command run to find out does not.
    assert _run_notice() == []
    assert check_installation_clients(HOST)["status"] == "warn"
