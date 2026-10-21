"""Folders an earlier version pulled from shared skills stay out of the library.

Shared (project, organization and integration) skill sync is parked with the
engine routes behind it. A folder it pulled is still on disk with its scope in
the sync state; pushing the personal library must skip it rather than upload it
into the library under its slug (the guard audit C045 added for shared sync).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.hook_runtime import write_state
from beakr_cli.main import internal_app as app

API = "https://api.test"
MD = "---\nname: handbook\ndescription: Rules\n---\nShared template.\n"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.mark.parametrize(
    ("folder", "state"),
    [
        ("handbook", {"scope": "project", "local_sha256": {}}),
        ("handbook", {"scope": "integration", "local_sha256": {}}),
        ("handbook.overlay", {"local_sha256": {}}),
    ],
)
@respx.mock
def test_a_folder_pulled_from_a_shared_skill_is_not_pushed_into_the_library(
    isolated_home, folder, state
):
    local = isolated_home / ".claude" / "skills" / folder
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text(MD + "Edit.\n")
    write_state("skills_sync.json", {"skills": {folder: state}})
    respx.get(f"{API}/v1/skills").mock(return_value=httpx.Response(200, json=[]))
    uploaded = respx.post(url__regex=rf"{API}/v1/(me/)?skills.*").mock(
        return_value=httpx.Response(201, json={"id": "u1"})
    )

    result = CliRunner().invoke(
        app, ["skills", "sync", "--json", "--target", "claude", "--direction", "push"]
    )

    assert result.exit_code == 0, result.output
    assert {a["slug"]: a["action"] for a in json.loads(result.stdout)} == {folder: "skipped"}
    assert not uploaded.called
