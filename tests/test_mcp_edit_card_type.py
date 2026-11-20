"""The stdio ``edit_card_type`` tool: what it sends, and what it refuses before sending.

The hosted server has the same tool (beakr-engine
``beakr/api/mcp_remote/card_type_tools.py``, tested there by
``tests/unit/test_mcp_edit_card_type.py``); these are the same promises for this
server, and the result texts are the hosted ones.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_cli import card_types, mcp_server, mcp_tools_platform, mcp_tools_projects

API = "https://api.test"
PROJECT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
URL = f"{API}/v1/knowledge/wiki/page-types/experiment"
SAVED = {
    "page_type": {
        "key": "experiment",
        "label": "Experiment",
        "status": "active",
        "scope": "project",
        "overrides": "global",
        "metadata_schema": {
            "key_attributes": ["Run date", "Operator"],
            "required_attributes": ["Run date"],
            "grain": "row",
        },
    }
}


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")
    mcp_tools_platform._resolved_projects.clear()
    yield
    mcp_tools_platform._resolved_projects.clear()


def _project() -> None:
    respx.get(f"{API}/v1/projects/{PROJECT}").respond(json={"id": PROJECT, "name": "Assays"})


async def test_the_tool_takes_the_hosted_tools_arguments() -> None:
    listed = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    schema = listed["edit_card_type"].input_schema
    assert set(schema["properties"]) == {
        "type",
        "project",
        "scope",
        "label",
        "description",
        "when_to_use",
        "when_not_to_use",
        "aliases",
        "fields",
        "grain",
        "status",
        "reset",
    }
    assert schema["required"] == ["type"]


@respx.mock
async def test_an_edit_is_one_patch_and_the_answer_says_what_it_left() -> None:
    _project()
    patch = respx.patch(URL).respond(json=SAVED)

    text = await mcp_tools_projects.edit_card_type(
        "experiment",
        project=PROJECT,
        fields=[{"name": "Run date", "required": True}, "Operator"],
        grain="row",
    )

    assert json.loads(patch.calls.last.request.content) == {
        "scope": "project",
        "project_id": PROJECT,
        "fields": [
            {"name": "Run date", "required": True},
            {"name": "Operator", "required": False},
        ],
        "grain": "row",
    }
    assert text.splitlines() == [
        "Changed the card type Experiment (experiment) for this project.",
        "Fields: Run date (required); Operator",
        "Each instance is a Facts row on the card it belongs to, not its own card.",
        "This is this project's own copy; the built-in type is unchanged. "
        "reset=true drops the copy.",
        "Cards already written are not rewritten; later compiles use this definition.",
    ]


@respx.mock
async def test_reset_removes_the_copy() -> None:
    _project()
    delete = respx.delete(URL).respond(json={"removed": True})

    text = await mcp_tools_projects.edit_card_type("experiment", project=PROJECT, reset=True)

    assert dict(delete.calls.last.request.url.params) == {
        "scope": "project",
        "project_id": PROJECT,
    }
    assert text == (
        "Removed the copy of the card type experiment for this project; "
        "it uses the type it inherits again."
    )


@respx.mock
@pytest.mark.parametrize("key", ["experiment/status", "../proposals", "a?b=c", "x#y", "a.b"])
async def test_a_key_that_could_name_another_route_is_refused_before_any_call(key) -> None:
    # The key becomes a path segment. "experiment/status" would otherwise reach
    # the status route with this tool's body.
    route = respx.route(url__startswith=API).respond(json={})

    result = await mcp_tools_projects.edit_card_type(key, project=PROJECT, status="deprecated")

    assert result == {
        "status": "error",
        "error_code": "invalid_arguments",
        "message": "A card type's key is letters, digits, spaces, hyphens and underscores.",
        "fix": "Pass `type` exactly as knowledge_base command='ontology' prints it.",
    }
    assert not route.called


@respx.mock
async def test_a_key_with_a_space_is_one_encoded_path_segment() -> None:
    _project()
    patch = respx.patch(f"{API}/v1/knowledge/wiki/page-types/lab%20note").respond(json=SAVED)

    await mcp_tools_projects.edit_card_type("lab note", project=PROJECT, label="Lab note")

    assert patch.calls.last.request.url.raw_path.endswith(b"/page-types/lab%20note")


@respx.mock
async def test_an_organization_wide_change_names_no_project() -> None:
    patch = respx.patch(URL).respond(json=SAVED)

    refused = await mcp_tools_projects.edit_card_type(
        "experiment", scope="org", project=PROJECT, grain="row"
    )
    sent = await mcp_tools_projects.edit_card_type("experiment", scope="org", grain="row")

    assert refused["error_code"] == "invalid_arguments"
    assert refused["message"] == "An organization-wide change names no project."
    assert "the organization" in sent
    assert patch.call_count == 1
    assert json.loads(patch.calls.last.request.content) == {"scope": "org", "grain": "row"}


@respx.mock
async def test_calls_with_nothing_to_send_are_refused() -> None:
    _project()
    route = respx.route(url__startswith=f"{API}/v1/knowledge").respond(json={})

    nothing = await mcp_tools_projects.edit_card_type("experiment", project=PROJECT)
    no_type = await mcp_tools_projects.edit_card_type("  ", project=PROJECT, label="X")
    no_project = await mcp_tools_projects.edit_card_type("experiment", label="X")
    bad_scope = await mcp_tools_projects.edit_card_type("experiment", scope="global", label="X")

    assert (nothing["message"], nothing["fix"]) == card_types.NOTHING_TO_CHANGE
    assert (no_type["message"], no_type["fix"]) == card_types.NO_TYPE
    assert (no_project["message"], no_project["fix"]) == card_types.NO_PROJECT
    assert (bad_scope["message"], bad_scope["fix"]) == card_types.BAD_SCOPE
    assert {r["error_code"] for r in (nothing, no_type, no_project, bad_scope)} == {
        "invalid_arguments"
    }
    assert not route.called


def test_a_turned_off_organization_type_reads_as_the_hosted_server_words_it() -> None:
    text = card_types.card_type_saved_text(
        {"key": "lot", "status": "deprecated", "overrides": "org"}, "org"
    )
    assert text.splitlines() == [
        "Changed the card type lot (lot) for the organization.",
        "It is turned off: compiles for the organization no longer use this type.",
        "This is the organization's own copy; the organization's type is unchanged. "
        "reset=true drops the copy.",
        "Cards already written are not rewritten; later compiles use this definition.",
    ]
