"""``beakr skills ls`` against a mocked API: every group of skills, the filters that
separate them, and each skill's labels and local sync state."""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
PROJECT = "22222222-2222-2222-2222-222222222222"
runner = CliRunner()

LIBRARY_SKILL = {
    "id": "sk-1",
    "slug": "release-notes",
    "name": "Release notes",
    "visibility": "org",
    "owner": "me",
    "origin": "created",
    "installed": True,
    "description": "Draft release notes from merged PRs.",
}
SCOPED_SKILL = {
    "id": "sk-2",
    "slug": "triage",
    "scope": "project",
    "integration": None,
    "overlay_ids": ["o-1"],
    "can_edit": False,
    "description": "Triage incoming bugs.",
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _envelope(rows: list[dict]) -> dict:
    return {"data": rows, "next_cursor": None, "has_more": False}


@respx.mock
def test_ls_lists_the_library_with_local_sync_state(isolated_home):
    route = respx.get(f"{API}/v1/skills").respond(json=_envelope([LIBRARY_SKILL]))
    # Installed for Claude only, never synced: "untracked" there, "absent" for Codex.
    installed = isolated_home / ".claude" / "skills" / "release-notes"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text("---\nname: release-notes\n---\n", encoding="utf-8")

    result = runner.invoke(app, ["skills", "ls", "--json"])

    assert result.exit_code == 0, result.output
    assert route.called
    assert json.loads(result.stdout) == [
        {**LIBRARY_SKILL, "local": {"claude": "untracked", "codex": "absent"}}
    ]


@respx.mock
def test_ls_renders_a_table_for_a_person(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    respx.get(f"{API}/v1/skills").respond(json=_envelope([LIBRARY_SKILL]))
    result = runner.invoke(app, ["skills", "ls"])
    assert result.exit_code == 0, result.output
    assert "release-notes" in result.stdout
    assert "absent" in result.stdout
    for label in ("org", "me", "created", "yes"):
        assert label in result.stdout


@respx.mock
def test_ls_empty_library_says_so(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    respx.get(f"{API}/v1/skills").respond(json=_envelope([]))
    result = runner.invoke(app, ["skills", "ls"])
    assert result.exit_code == 0, result.output
    assert "No skills match." in result.stderr


@respx.mock
def test_ls_sends_the_group_and_installed_filters():
    route = respx.get(f"{API}/v1/skills").respond(json=_envelope([]))
    result = runner.invoke(
        app,
        [
            "skills",
            "ls",
            "--mine",
            "--public",
            "--builtin",
            "--available",
            "--origin",
            "mined",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    params = route.calls.last.request.url.params
    assert params.get_list("owner") == ["me", "anyone", "system"]
    assert params.get_list("origin") == ["mined"]
    assert params["installed"] == "false"


@respx.mock
def test_ls_without_flags_sends_no_filter():
    route = respx.get(f"{API}/v1/skills").respond(json=_envelope([]))
    assert runner.invoke(app, ["skills", "ls", "--json"]).exit_code == 0
    assert set(route.calls.last.request.url.params) == {"limit"}


def test_ls_refuses_an_unknown_origin():
    result = runner.invoke(app, ["skills", "ls", "--origin", "found"])
    assert result.exit_code == 2
    assert "created, mined, imported, system" in result.output
