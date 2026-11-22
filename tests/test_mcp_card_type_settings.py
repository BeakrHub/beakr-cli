"""The stdio ``card_type_settings`` tool and ``create_project``'s ``card_types``.

The hosted server has the same (beakr-engine
``beakr/api/mcp_remote/card_settings_tools.py`` and ``project_tools.py``, tested
there by ``tests/unit/test_mcp_card_type_settings.py``); these are the same
promises for this server, and every expected text is the hosted one.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_cli import card_settings, mcp_server, mcp_tools_platform, mcp_tools_projects
from beakr_cli.card_settings import CREATE_REFUSALS, REFUSALS, settings_text
from tests import mcp_client

API = "https://api.test"
PROJECT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
SOURCE = "9f8e7d6c-5b4a-4c3d-8e2f-1a0b9c8d7e6f"
SETTINGS_URL = f"{API}/v1/knowledge/wiki/page-types/settings"
EXPERIMENT = {"key": "experiment", "label": "Experiment", "scope": "global", "live_cards": 12}
DECISION = {"key": "decision", "label": "Decision", "scope": "global", "live_cards": 0}
LOT = {"key": "lot", "label": "Lot", "scope": "org", "live_cards": 1}
PREVIEWED = {
    "project_id": PROJECT,
    "dry_run": True,
    "current": {"inherit": "organization", "agent_may_change": True},
    "requested": {"inherit": "own", "agent_may_change": False},
    "kept_types": ["index", "organization", "overview", "person", "topic"],
    "types_dropped": [EXPERIMENT, DECISION, LOT],
    "types_kept": [],
    "usable_own_types": ["compound"],
    "history_id": None,
    "undo_event_id": None,
    "suggestions": 0,
}


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")
    mcp_tools_platform._resolved_projects.clear()
    yield
    mcp_tools_platform._resolved_projects.clear()


def _project(project_id: str = PROJECT) -> None:
    respx.get(f"{API}/v1/projects/{project_id}").respond(json={"id": project_id, "name": "Assays"})


async def test_the_tools_take_the_hosted_tools_arguments() -> None:
    listed = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    settings = listed["card_type_settings"].input_schema
    assert set(settings["properties"]) == {
        "project",
        "inherit",
        "agent_may_change",
        "keep_types_in_use",
        "apply",
    }
    assert settings["required"] == ["project"]
    assert set(listed["create_project"].input_schema["properties"]) == {
        "name",
        "description",
        "card_types",
    }


@respx.mock
async def test_without_apply_it_previews_and_says_what_would_drop_out() -> None:
    _project()
    route = respx.put(SETTINGS_URL).respond(json=PREVIEWED)

    text = await mcp_tools_projects.card_type_settings(
        PROJECT, inherit="own", agent_may_change=False
    )

    assert json.loads(route.calls.last.request.content) == {
        "inherit": "own",
        "agent_may_change": False,
        "project_id": PROJECT,
        "keep_types_in_use": False,
        "dry_run": True,
    }
    assert text == (
        "Preview only: nothing was changed.\n"
        "This project would be offered only its own card types.\n"
        "It keeps these built-in types: index, organization, overview, person, topic.\n"
        "Would no longer be offered in this project: Experiment (experiment, 12 cards); "
        "Decision (decision, no cards); Lot (lot, 1 card).\n"
        "Their cards are not changed and stay readable; switching back offers the types "
        "again.\n"
        "To keep the types that have cards as the project's own types, pass "
        "keep_types_in_use=true.\n"
        "Beakr would no longer add or change this project's card types; only people would.\n"
        "Show the person which card types would drop out and how many cards each has. If "
        "they agree, call this again with apply=true to make the change."
    )


@respx.mock
@pytest.mark.parametrize("apply", [None, False, "true", 1])
async def test_only_apply_true_writes(apply) -> None:
    # A truthy string or a number is not the person's agreement. Sent through
    # the server, so argument validation cannot turn "true" into True either.
    _project()
    route = respx.put(SETTINGS_URL).respond(json=PREVIEWED)

    arguments = {"scope": PROJECT, "inherit": "builtin"}
    result = await mcp_client.call_tool(
        "card_type_settings", arguments if apply is None else {**arguments, "apply": apply}
    )

    assert not result.is_error, result.content[0].text
    assert json.loads(route.calls.last.request.content)["dry_run"] is True


@respx.mock
async def test_apply_and_keep_types_in_use_are_sent_only_when_true() -> None:
    _project()
    route = respx.put(SETTINGS_URL).respond(json=PREVIEWED | {"dry_run": False})

    await mcp_tools_projects.card_type_settings(
        PROJECT, inherit="own", keep_types_in_use=True, apply=True
    )

    assert json.loads(route.calls.last.request.content) == {
        "inherit": "own",
        "project_id": PROJECT,
        "keep_types_in_use": True,
        "dry_run": False,
    }


@respx.mock
@pytest.mark.parametrize(
    ("args", "refusal"),
    [
        ({}, "no_change"),
        ({"inherit": " "}, "no_change"),
        ({"inherit": "everything"}, "bad_inherit"),
        ({"agent_may_change": "no"}, "bad_agent_may_change"),
    ],
)
async def test_a_call_that_cannot_be_sent_is_refused_before_any_call(args, refusal) -> None:
    route = respx.route(url__startswith=API).respond(json={})

    result = await mcp_tools_projects.card_type_settings(PROJECT, **args)

    message, fix = REFUSALS[refusal]
    assert result == {
        "status": "error",
        "error_code": "invalid_arguments",
        "message": message,
        "fix": fix,
    }
    assert not route.called


def test_an_applied_change_names_the_kept_and_dropped_types_and_the_suggestions() -> None:
    kept = settings_text(
        PREVIEWED | {"dry_run": False, "types_dropped": [DECISION], "types_kept": [EXPERIMENT, LOT]}
    )
    assert kept == (
        "This project is now offered only its own card types.\n"
        "It keeps these built-in types: index, organization, overview, person, topic.\n"
        "Now the project's own card types, because cards carry them: Experiment "
        "(experiment, 12 cards); Lot (lot, 1 card).\n"
        "No longer offered in this project: Decision (decision, no cards).\n"
        "Their cards are not changed and stay readable; switching back offers the types "
        "again.\n"
        "Beakr no longer adds or changes this project's card types; only people do.\n"
        "It shows in the project's Activity and in its card type history."
    )
    dropped = settings_text(PREVIEWED | {"dry_run": False, "suggestions": 2})
    assert dropped.splitlines()[-2:] == [
        "Beakr recorded 2 suggestions to move the cards of the dropped types to another "
        "type, for a person to apply or dismiss.",
        "It shows in the project's Activity and in its card type history.",
    ]


def test_a_change_to_who_may_change_types_alone_and_one_that_is_already_so() -> None:
    answer = PREVIEWED | {
        "requested": {"inherit": "organization", "agent_may_change": False},
        "types_dropped": [],
        "kept_types": [],
    }
    assert settings_text(answer) == (
        "Preview only: nothing was changed.\n"
        "Beakr would no longer add or change this project's card types; only people would.\n"
        "Show the person this. If they agree, call this again with apply=true to make the "
        "change."
    )
    back = PREVIEWED | {
        "dry_run": False,
        "current": {"inherit": "own", "agent_may_change": False},
        "requested": {"inherit": "organization", "agent_may_change": True},
        "types_dropped": [],
        "kept_types": [],
    }
    assert settings_text(back) == (
        "This project is now offered the organization's card types and the built-in ones.\n"
        "No card type dropped out of this project.\n"
        "Beakr can add and change this project's card types.\n"
        "It shows in the project's Activity and in its card type history."
    )
    same = PREVIEWED | {"requested": PREVIEWED["current"], "types_dropped": [], "kept_types": []}
    assert settings_text(same) == (
        "The project's card type settings already say this; nothing was changed."
    )


def test_the_commands_words_name_its_flags() -> None:
    text = settings_text(PREVIEWED, card_settings.COMMAND_WORDS)
    assert "pass --keep-types-in-use." in text
    assert text.splitlines()[-1] == "Run this again with --apply to make the change."
    assert "apply=true" not in text and "keep_types_in_use=true" not in text


# --- create_project's card_types ------------------------------------------------------


def _no_projects() -> None:
    respx.get(f"{API}/v1/projects").respond(json={"data": [], "has_more": False})


@respx.mock
async def test_create_project_without_card_types_sends_and_answers_what_it_always_did() -> None:
    _no_projects()
    created = respx.post(f"{API}/v1/projects").respond(201, json={"id": PROJECT, "name": "Assays"})

    text = await mcp_tools_projects.create_project("Assays")

    assert json.loads(created.calls.last.request.content) == {"name": "Assays"}
    assert text == mcp_tools_projects.created_text({"id": PROJECT, "name": "Assays"}, "Assays")
    assert "card types" not in text


@respx.mock
async def test_create_project_sends_the_card_type_choices_and_says_them() -> None:
    _no_projects()
    _project(SOURCE)
    created = respx.post(f"{API}/v1/projects").respond(201, json={"id": PROJECT, "name": "Assays"})

    text = await mcp_tools_projects.create_project(
        "Assays",
        card_types={"inherit": "own", "agent_may_change": False, "copy_types_from": SOURCE},
    )

    assert json.loads(created.calls.last.request.content) == {
        "name": "Assays",
        "card_types": {
            "inherit": "own",
            "agent_may_change": False,
            "copy_from_project_id": SOURCE,
        },
    }
    assert text.splitlines()[:4] == [
        f"Created project 'Assays' (id: {PROJECT}).",
        "Its card types were copied from the project you named.",
        "It is offered only its own card types.",
        "Beakr does not add or change its card types; only people do.",
    ]


@respx.mock
async def test_create_project_refuses_own_without_types_to_copy_and_an_unknown_choice() -> None:
    _no_projects()
    created = respx.post(f"{API}/v1/projects")

    own = await mcp_tools_projects.create_project("Assays", card_types={"inherit": "own"})
    unknown = await mcp_tools_projects.create_project("Assays", card_types={"inherit": "all"})

    assert (own["message"], own["fix"]) == CREATE_REFUSALS["own_without_types"]
    assert (unknown["message"], unknown["fix"]) == CREATE_REFUSALS["bad_inherit"]
    assert own["error_code"] == unknown["error_code"] == "invalid_arguments"
    assert not created.called
