"""``beakr kb types ls|edit|reset|copy|promote`` over /v1/knowledge/wiki/page-types.

``ls`` shows a project's card types and marks a copy that stands in front of a
broader type; ``edit`` sends only what is given, with the fields in the order
given; ``reset`` removes a scope's own copy. A key that is not one path segment
is refused before any request, and an organization-wide change (``--org-wide``)
names no project. The project is -P, --project, --scope or -s, as on every kb
command, so the API's ``scope`` is not a flag here. ``copy`` and ``promote``
print what was copied, replaced and skipped, with why.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "11111111-1111-4111-8111-111111111111"
LIST_URL = f"{API}/v1/knowledge/wiki/page-types"
URL = f"{LIST_URL}/experiment"
runner = CliRunner()
EXPERIMENT = {
    "key": "experiment",
    "label": "Experiment",
    "description": "One run of an assay",
    "status": "active",
    "scope": "project",
    "overrides": "global",
    "aliases": ["run"],
    "metadata_schema": {
        "key_attributes": ["Run date", "Operator"],
        "required_attributes": ["Run date"],
        "grain": "row",
    },
}
LISTING = {
    "map_mode": True,
    "page_types": [
        EXPERIMENT,
        {"key": "supplier", "label": "Supplier", "status": "active", "scope": "org"},
        {"key": "person", "label": "Person", "status": "active", "scope": "global"},
        {
            "key": "lot",
            "label": "Lot",
            "status": "active",
            "scope": "project",
            "overrides": "org",
        },
    ],
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    # No bound project: a command given none must say so, not read this checkout's.
    monkeypatch.delenv("BEAKR_PROJECT_ID", raising=False)


def _invoke(*args: str, mode: str = "human", **kwargs):
    return runner.invoke(app, list(args), env={"BEAKR_OUTPUT": mode, "COLUMNS": "200"}, **kwargs)


@respx.mock
@pytest.mark.parametrize("mode", ["human", "compact"])
def test_ls_shows_each_type_and_marks_a_copy(mode) -> None:
    route = respx.get(LIST_URL).respond(200, json=LISTING)
    result = _invoke("kb", "types", "ls", "-P", PROJECT, mode=mode)
    assert result.exit_code == 0, result.output
    assert dict(route.calls.last.request.url.params) == {"project_id": PROJECT}
    assert "experiment" in result.output and "Run date (required); Operator" in result.output
    assert "project (copy of built-in)" in result.output
    assert "project (copy of organization type)" in result.output
    assert "organization" in result.output and "built-in" in result.output


@respx.mock
def test_ls_json_is_the_answer_as_returned() -> None:
    respx.get(LIST_URL).respond(200, json=LISTING)
    result = _invoke("kb", "types", "ls", "-P", PROJECT, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == LISTING


@respx.mock
def test_edit_sends_only_what_is_given_with_the_fields_in_order() -> None:
    route = respx.patch(URL).respond(200, json={"page_type": EXPERIMENT})
    result = _invoke(
        "kb",
        "types",
        "edit",
        "experiment",
        "-P",
        PROJECT,
        "--field",
        "Run date",
        "--field",
        "Operator",
        "--required-field",
        "run date",
        "--required-field",
        "Instrument",
        "--alias",
        "run",
        "--grain",
        "row",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "scope": "project",
        "project_id": PROJECT,
        "aliases": ["run"],
        "fields": [
            {"name": "Run date", "required": True},
            {"name": "Operator", "required": False},
            {"name": "Instrument", "required": True},
        ],
        "grain": "row",
    }
    assert "Changed the card type Experiment (experiment) for this project." in result.output
    assert "Fields: Run date (required); Operator" in result.output
    assert "Scope: project (copy of built-in)" in result.output


@respx.mock
def test_edit_piped_names_the_command_that_drops_the_copy() -> None:
    respx.patch(URL).respond(200, json={"page_type": EXPERIMENT})
    result = _invoke(
        "kb", "types", "edit", "experiment", "-P", PROJECT, "--label", "Experiment", mode="compact"
    )
    assert result.exit_code == 0, result.output
    assert f"next: beakr kb types reset experiment -P {PROJECT}" in result.stdout


@respx.mock
def test_edit_for_the_organization_names_no_project() -> None:
    route = respx.patch(URL).respond(200, json={"page_type": {**EXPERIMENT, "scope": "org"}})
    result = _invoke(
        "kb", "types", "edit", "experiment", "--org-wide", "--status", "deprecated", "--json"
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "scope": "org",
        "status": "deprecated",
    }
    assert json.loads(result.stdout)["page_type"]["scope"] == "org"
    refused = _invoke(
        "kb", "types", "edit", "experiment", "--org-wide", "-P", PROJECT, "--label", "X"
    )
    assert refused.exit_code == 2
    assert "An organization-wide change names no project." in refused.output
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("key", ["experiment/status", "../proposals", "a?b=c", "x#y", "a.b"])
@pytest.mark.parametrize("command", ["edit", "reset"])
def test_a_key_that_could_name_another_route_is_refused_before_any_request(command, key) -> None:
    # The key becomes a path segment: "experiment/status" would otherwise reach
    # the status route with this command's body.
    route = respx.route(url__startswith=API).respond(200, json={})
    extra = ["--status", "deprecated"] if command == "edit" else []
    result = _invoke("kb", "types", command, "-P", PROJECT, *extra, "--", key)
    assert result.exit_code == 2
    assert "letters, digits, spaces, hyphens and underscores" in result.output
    assert not route.called


@respx.mock
def test_a_key_with_a_space_is_one_encoded_path_segment() -> None:
    route = respx.patch(f"{LIST_URL}/lab%20note").respond(
        200, json={"page_type": {"key": "lab note", "label": "Lab note", "scope": "project"}}
    )
    result = _invoke("kb", "types", "edit", "lab note", "-P", PROJECT, "--label", "Lab note")
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.raw_path.endswith(b"/page-types/lab%20note")


@respx.mock
def test_edit_refuses_bad_values_and_an_empty_change_before_any_request() -> None:
    route = respx.patch(URL).respond(200, json={"page_type": EXPERIMENT})
    for extra in ([], ["--grain", "table"], ["--status", "off"]):
        result = _invoke("kb", "types", "edit", "experiment", "-P", PROJECT, *extra)
        assert result.exit_code == 2, extra
    assert not route.called


def test_edit_without_a_project_says_how_to_name_one(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BEAKR_CONFIG_DIR", str(tmp_path))
    result = _invoke("kb", "types", "edit", "experiment", "--label", "X")
    assert result.exit_code == 2
    assert "--project" in result.output


@respx.mock
def test_reset_removes_the_scopes_copy() -> None:
    route = respx.delete(URL).respond(
        200, json={"removed": True, "key": "experiment", "scope": "project"}
    )
    result = _invoke("kb", "types", "reset", "experiment", "-P", PROJECT)
    assert result.exit_code == 0, result.output
    assert dict(route.calls.last.request.url.params) == {"scope": "project", "project_id": PROJECT}
    assert "it uses the type it inherits again" in result.output
    as_json = _invoke("kb", "types", "reset", "experiment", "--org-wide", "--json")
    assert as_json.exit_code == 0, as_json.output
    assert dict(route.calls.last.request.url.params) == {"scope": "org"}
    assert json.loads(as_json.stdout) == {"removed": True, "key": "experiment", "scope": "project"}


@respx.mock
def test_reset_of_a_type_defined_only_in_the_scope_is_a_conflict() -> None:
    respx.delete(URL).respond(
        409,
        json={
            "error": {
                "code": "conflict",
                "message": "This type is defined only for this project; turn it off instead.",
            }
        },
    )
    result = _invoke("kb", "types", "reset", "experiment", "-P", PROJECT)
    assert result.exit_code == 5
    assert "turn it off instead" in result.output


@respx.mock
def test_the_project_is_also_spelled_scope_and_s_as_on_every_kb_command() -> None:
    route = respx.get(LIST_URL).respond(200, json=LISTING)
    for flag in ("--scope", "-s"):
        result = _invoke("kb", "types", "ls", flag, PROJECT)
        assert result.exit_code == 0, result.output
        assert dict(route.calls.last.request.url.params) == {"project_id": PROJECT}


OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.mark.real_project_refs
@respx.mock
def test_copy_resolves_both_projects_by_name_and_says_what_happened() -> None:
    respx.get(f"{API}/v1/projects").respond(
        json={
            "data": [{"id": PROJECT, "name": "Basel"}, {"id": OTHER, "name": "Lyon"}],
            "has_more": False,
        }
    )
    route = respx.post(f"{LIST_URL}/copy").respond(
        200,
        json={
            "copied": [EXPERIMENT],
            "replaced": [],
            "skipped": [
                {"key": "lot", "reason": "exists"},
                {"key": "capa", "reason": "not_found"},
            ],
        },
    )
    result = _invoke(
        "kb", "types", "copy", "experiment", "lot", "capa", "--from", "Basel", "--to", "Lyon"
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "from_project_id": PROJECT,
        "to_project_id": OTHER,
        "keys": ["experiment", "lot", "capa"],
        "replace": False,
    }
    assert "Copied: experiment" in result.output
    assert "Skipped lot: the destination already defines it; pass --replace" in result.output
    assert "Skipped capa: the source project does not define it" in result.output


@respx.mock
def test_copy_piped_is_one_line_a_key_and_json_is_the_answer() -> None:
    answer = {
        "copied": [],
        "replaced": [EXPERIMENT],
        "skipped": [{"key": "capa", "reason": "not_found"}],
    }
    route = respx.post(f"{LIST_URL}/copy").respond(200, json=answer)
    args = ("kb", "types", "copy", "experiment", "capa", "--from", PROJECT, "--to", OTHER)
    piped = _invoke(*args, "--replace", mode="compact")
    assert piped.exit_code == 0, piped.output
    assert json.loads(route.calls.last.request.content)["replace"] is True
    assert piped.stdout.splitlines() == ["replaced\texperiment", "skipped\tcapa\tnot_found"]
    as_json = _invoke(*args, "--json")
    assert json.loads(as_json.stdout) == answer


def test_copy_needs_both_projects() -> None:
    assert _invoke("kb", "types", "copy", "experiment", "--from", PROJECT).exit_code == 2
    assert _invoke("kb", "types", "copy", "experiment", "--to", PROJECT).exit_code == 2
    assert _invoke("kb", "types", "copy", "--from", PROJECT, "--to", OTHER).exit_code == 2


@respx.mock
def test_promote_sends_the_project_as_source() -> None:
    # The route names the project `source`: a body naming `project_id` is not
    # what it takes, and it would be judged as a write to one project.
    route = respx.post(f"{LIST_URL}/promote").respond(
        200,
        json={
            "copied": [{**EXPERIMENT, "scope": "org"}],
            "replaced": [],
            "skipped": [{"key": "lot", "reason": "exists"}],
        },
    )
    result = _invoke("kb", "types", "promote", "experiment", "lot", "-P", PROJECT)
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "source": PROJECT,
        "keys": ["experiment", "lot"],
        "replace": False,
    }
    assert "Copied: experiment" in result.output
    assert "Skipped lot: the organization already defines it; pass --replace" in result.output


@respx.mock
def test_promote_refused_for_a_non_admin_exits_as_an_auth_error() -> None:
    respx.post(f"{LIST_URL}/promote").respond(
        403,
        json={"error": {"code": "forbidden", "message": "Only an organization admin may."}},
    )
    result = _invoke("kb", "types", "promote", "experiment", "-P", PROJECT)
    assert result.exit_code == 3
    assert "organization admin" in result.output
