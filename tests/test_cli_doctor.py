"""``beakr doctor``."""

from __future__ import annotations

import json
from pathlib import Path

import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from beakr_cli.project_binding import write_binding

API = "https://api.test"


def _checks(output: str) -> dict[str, dict]:
    return {c["name"]: c for c in json.loads(output)["checks"]}


@respx.mock
def test_doctor_reports_scoped_key_and_binding(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_testkey123")
    write_binding(Path.cwd(), {"project_id": "p1", "name": "Platform"})
    respx.get(f"{API}/v1/public/openapi.json").respond(404)
    respx.get(f"{API}/v1/me").respond(json={"user": {"display_name": "Ada"}})
    respx.get(f"{API}/v1/me/api-keys").respond(
        json=[
            {"id": "k0", "key_prefix": "bk_live_other", "scopes": None},
            {"id": "k1", "key_prefix": "bk_live_test", "scopes": ["kb:read"]},
        ]
    )

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    checks = _checks(result.stdout)
    assert json.loads(result.stdout)["ok"] is True
    assert checks["api"]["status"] == "ok"
    assert checks["auth"]["detail"].startswith("Ada via env")
    assert checks["scopes"]["scopes"] == ["kb:read"]
    assert checks["project"]["status"] == "ok"
    assert checks["mcp"]["status"] == "warn"


@respx.mock
def test_doctor_fails_without_credentials(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    respx.get(f"{API}/v1/public/openapi.json").respond(200, json={})

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    checks = _checks(result.stdout)
    assert checks["auth"]["status"] == "fail"
    assert checks["project"]["status"] == "warn"


@respx.mock
def test_doctor_fails_when_api_unreachable(monkeypatch):
    import httpx

    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_x")
    # The SDK retries connection errors with backoff; do not wait it out.
    monkeypatch.setattr("beakr_sdk.client.time.sleep", lambda _seconds: None)
    respx.get(f"{API}/v1/public/openapi.json").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{API}/v1/me").mock(side_effect=httpx.ConnectError("down"))

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert _checks(result.stdout)["api"]["status"] == "fail"


def test_doctor_reports_a_stuck_hook_queue(monkeypatch):
    import os
    import time

    from beakr_cli import hook_queue
    from beakr_cli.commands.doctor import check_hook_queue

    assert check_hook_queue()["status"] == "ok"
    path = hook_queue.enqueue({"key": "claude-code:s", "body": {"messages": []}})
    assert check_hook_queue()["queued"] == 1
    assert check_hook_queue()["status"] == "ok"
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(path, (two_days_ago, two_days_ago))
    stale = check_hook_queue()
    assert stale["status"] == "warn"
    assert stale["healthy"] is False


def test_hooks_check_reports_installed_hooks(monkeypatch):
    # Root cause: check_hooks passed the hooks status as ``status=`` to _check,
    # whose own ``status`` parameter is the check's verdict, so every call raised
    # TypeError and the check could never report installed hooks.
    from beakr_cli.commands import doctor, hooks

    monkeypatch.setattr(
        hooks,
        "hooks_status",
        lambda: {"claude": {"installed": True}, "codex": {"installed": False}},
    )
    check = doctor.check_hooks()
    assert check["status"] == "ok"
    assert check["detail"] == "installed for claude"
    assert check["hooks"]["claude"]["installed"] is True

    monkeypatch.setattr(hooks, "hooks_status", lambda: {"claude": {"installed": False}})
    assert doctor.check_hooks()["status"] == "warn"
