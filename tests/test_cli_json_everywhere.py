"""Every command takes ``--json`` (spec W7 item 2; ``beakr --help`` promises it).

Root cause this guards: the root help said "Every command takes --json", but 24
commands (``projects rm``, ``auth logout``, ``runs cancel``, ``setup`` ...) had
no such option, so ``beakr projects rm x --json`` failed with "No such option"
and a script could not rely on the flag. The gate walks the whole Typer tree,
so a new command without ``--json`` fails here rather than in someone's script.
"""

from __future__ import annotations

import json

import click
import pytest
import respx
import typer
from typer.testing import CliRunner

# Every command the CLI has, the unlisted ones too (beakr_cli.main.internal_app).
from beakr_cli.main import internal_app as app

API = "https://api.test"
runner = CliRunner()

#: Commands that cannot take ``--json``, each with the reason. Keep this short:
#: an entry needs a reason a script could not be served by structured output.
JSON_EXEMPT_COMMANDS = {
    # stdout *is* the MCP stdio protocol; anything else written there corrupts it.
    "mcp": "stdout carries the MCP protocol",
    # Passes every argument through to `codex` unchanged; `codex exec --json`
    # is Codex's own flag, so taking --json here would steal it.
    "codex": "arguments pass through to the codex binary, which has its own --json",
}


def _leaf_commands() -> dict[str, click.Command]:
    leaves: dict[str, click.Command] = {}

    def walk(command: click.Command, path: list[str]) -> None:
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                walk(sub, [*path, name])
        else:
            leaves[" ".join(path)] = command

    walk(typer.main.get_command(app), [])
    return leaves


def _option_names(command: click.Command) -> set[str]:
    return {opt for param in command.params for opt in getattr(param, "opts", [])}


def test_every_command_takes_json_unless_exempt_with_a_reason():
    leaves = _leaf_commands()
    missing = sorted(
        path
        for path, command in leaves.items()
        if "--json" not in _option_names(command) and path not in JSON_EXEMPT_COMMANDS
    )
    assert missing == [], f"commands without --json: {missing}"


def test_the_exemption_list_names_only_real_commands_that_lack_json():
    # A stale entry would silently exempt a future command of the same name.
    leaves = _leaf_commands()
    for path in JSON_EXEMPT_COMMANDS:
        assert path in leaves, f"exempt command {path!r} no longer exists"
        assert "--json" not in _option_names(leaves[path]), f"{path!r} now takes --json"


@pytest.fixture
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _json(result) -> object:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@respx.mock
def test_projects_rm_json_prints_the_deleted_project(authenticated):
    project_id = "11111111-1111-1111-1111-111111111111"
    respx.get(f"{API}/v1/projects").respond(
        json={
            "data": [{"id": project_id, "name": "Alpha", "org_id": "o"}],
            "next_cursor": None,
            "has_more": False,
        }
    )
    deleted = respx.delete(f"{API}/v1/projects/{project_id}").respond(204)
    body = _json(runner.invoke(app, ["projects", "rm", project_id, "--yes", "--json"]))
    assert deleted.called
    assert body == {"id": project_id, "name": "Alpha", "deleted": True}


@respx.mock
def test_runs_cancel_json_prints_the_outcome(authenticated):
    respx.post(f"{API}/v1/runs/run-1/cancel").respond(204)
    body = _json(runner.invoke(app, ["runs", "cancel", "run-1", "--json"]))
    assert body == {"run_id": "run-1", "cancel_requested": True}


def test_auth_set_org_and_clear_org_json():
    assert _json(runner.invoke(app, ["auth", "set-org", "acme", "--json"])) == {"org_id": "acme"}
    assert _json(runner.invoke(app, ["auth", "clear-org", "--json"])) == {
        "org_id": None,
        "cleared": True,
    }


def test_auth_set_scope_json_with_a_project():
    body = _json(runner.invoke(app, ["auth", "set-scope", "--project", "p-1", "--json"]))
    assert body == {"project_id": "p-1"}


def test_auth_dev_json_skips_the_project_question_and_keeps_stdout_json():
    result = runner.invoke(app, ["auth", "dev", "--identity", "i-1", "--email", "a@b.c", "--json"])
    body = _json(result)
    assert body == {
        "dev_identity_id": "i-1",
        "email": "a@b.c",
        "api_url": "http://localhost:8000",
        "default_project": None,
    }


def test_auth_logout_json(monkeypatch):
    from beakr_cli.commands import auth

    monkeypatch.setattr(auth, "_revoke_stored_tokens", lambda origin: False)
    body = _json(runner.invoke(app, ["auth", "logout", "--json"]))
    assert body["logged_out"] is True
    assert body["tokens_revoked"] is False


def test_version_offline_json():
    from beakr_cli import __version__

    assert _json(runner.invoke(app, ["version", "--offline", "--json"])) == {"version": __version__}


def test_setup_json_sends_progress_to_stderr_and_a_summary_to_stdout(isolated_home):
    (isolated_home / ".claude").mkdir()
    args = ["setup", "--client", "claude", "--no-auth", "--no-skills", "--no-mcp", "--no-hooks"]
    result = runner.invoke(app, [*args, "--json"])
    assert _json(result) == {
        "action": "install",
        "clients": ["claude"],
        "scope": "user",
        "auth": False,
        "skills": False,
        "mcp": False,
        "hooks": False,
    }
    # The human progress still reaches the person, on stderr.
    assert "Done." in result.stderr
