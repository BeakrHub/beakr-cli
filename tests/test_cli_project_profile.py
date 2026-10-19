"""``beakr projects profile show|set|edit`` over /v1/projects/{id}/knowledge-profile.

``set --file`` replaces the whole profile (PUT) from JSON or YAML and refuses
unknown fields before any request; field flags change only those fields (PATCH)
and create the profile when the project has none; ``edit`` round-trips YAML
through $EDITOR. A project without a profile is exit 4 with what to run.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "11111111-1111-4111-8111-111111111111"
URL = f"{API}/v1/projects/{PROJECT}/knowledge-profile"
runner = CliRunner()
PROFILE = {
    "project_id": PROJECT,
    "name": "Basel",
    "description": "Batch records for the Basel site",
    "industry": "",
    "goals": ["Track deviations"],
    "focus_areas": [],
    "core_people": [{"name": "Priya", "role": "Owner", "relevance": ""}],
    "core_organizations": [],
    "core_topics": [],
    "entity_types": ["batch", "deviation"],
    "extraction_guidance": "",
    "created_at": "2026-10-19T00:00:00Z",
    "updated_at": "2026-10-19T00:00:00Z",
}
MISSING = httpx.Response(
    404, json={"error": {"code": "knowledge_profile_not_found", "message": "No profile yet."}}
)


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _invoke(*args: str, mode: str = "human", **kwargs):
    return runner.invoke(app, list(args), env={"BEAKR_OUTPUT": mode, "COLUMNS": "200"}, **kwargs)


@respx.mock
@pytest.mark.parametrize("mode", ["human", "compact"])
def test_show_prints_the_profile(mode) -> None:
    respx.get(URL).respond(200, json=PROFILE)
    result = _invoke("projects", "profile", "show", "-P", PROJECT, mode=mode)
    assert result.exit_code == 0, result.output
    assert "batch" in result.output and "deviation" in result.output
    assert "Priya" in result.output


@respx.mock
def test_show_without_a_profile_says_what_to_run() -> None:
    respx.get(URL).mock(return_value=MISSING)
    result = _invoke("projects", "profile", "show", "-P", PROJECT)
    assert result.exit_code == 4
    assert "beakr projects profile set" in result.output


@respx.mock
def test_set_from_a_yaml_file_puts_the_whole_profile(tmp_path) -> None:
    route = respx.put(URL).respond(200, json=PROFILE)
    path = tmp_path / "profile.yaml"
    path.write_text(
        "description: Batch records for the Basel site\n"
        "entity_types: [batch, deviation]\n"
        "core_people:\n  - {name: Priya, role: Owner}\n"
    )
    result = _invoke("projects", "profile", "set", "-P", PROJECT, "--file", str(path))
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "description": "Batch records for the Basel site",
        "entity_types": ["batch", "deviation"],
        "core_people": [{"name": "Priya", "role": "Owner"}],
    }


@respx.mock
def test_set_refuses_a_file_with_unknown_fields_before_any_request(tmp_path) -> None:
    route = respx.put(URL).respond(200, json=PROFILE)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"entity_type": ["batch"]}))
    result = _invoke("projects", "profile", "set", "-P", PROJECT, "--file", str(path))
    assert result.exit_code == 2
    assert "entity_type" in result.output
    assert not route.called


@respx.mock
def test_set_flags_patch_only_those_fields() -> None:
    route = respx.patch(URL).respond(200, json=PROFILE)
    result = _invoke(
        "projects",
        "profile",
        "set",
        "-P",
        PROJECT,
        "-t",
        "batch",
        "-t",
        "capa",
        "--person",
        "Priya:Owner",
        "--description",
        "Basel",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "description": "Basel",
        "entity_types": ["batch", "capa"],
        "core_people": [{"name": "Priya", "role": "Owner"}],
    }


@respx.mock
def test_set_flags_create_the_profile_when_the_project_has_none() -> None:
    respx.patch(URL).mock(return_value=MISSING)
    put = respx.put(URL).respond(201, json=PROFILE)
    result = _invoke("projects", "profile", "set", "-P", PROJECT, "-t", "batch")
    assert result.exit_code == 0, result.output
    assert json.loads(put.calls.last.request.content) == {"entity_types": ["batch"]}


def test_set_needs_a_file_or_flags_but_not_both(tmp_path) -> None:
    assert _invoke("projects", "profile", "set", "-P", PROJECT).exit_code == 2
    path = tmp_path / "p.json"
    path.write_text("{}")
    both = _invoke("projects", "profile", "set", "-P", PROJECT, "--file", str(path), "-t", "x")
    assert both.exit_code == 2


@respx.mock
def test_edit_round_trips_yaml_through_the_editor(monkeypatch) -> None:
    respx.get(URL).respond(200, json=PROFILE)
    put = respx.put(URL).respond(200, json=PROFILE)
    seen: list[str] = []

    def fake_edit(text: str, extension: str) -> str:
        seen.append(text)
        return text.replace("- deviation", "- deviation\n- supplier")

    monkeypatch.setattr("beakr_cli.commands.project_profile.click.edit", fake_edit)
    result = _invoke("projects", "profile", "edit", "-P", PROJECT)
    assert result.exit_code == 0, result.output
    assert "entity_types:" in seen[0]
    body = json.loads(put.calls.last.request.content)
    assert body["entity_types"] == ["batch", "deviation", "supplier"]
    assert body["core_people"] == [{"name": "Priya", "role": "Owner", "relevance": ""}]


@respx.mock
def test_edit_without_changes_sends_nothing(monkeypatch) -> None:
    respx.get(URL).mock(return_value=MISSING)
    put = respx.put(URL).respond(200, json=PROFILE)
    monkeypatch.setattr(
        "beakr_cli.commands.project_profile.click.edit", lambda text, extension: text
    )
    result = _invoke("projects", "profile", "edit", "-P", PROJECT)
    assert result.exit_code == 0, result.output
    assert not put.called
