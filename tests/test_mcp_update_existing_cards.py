"""The stdio ``update_existing_cards`` tool: what it sends, refuses and answers.

The hosted server has the same tool (beakr-engine
``beakr/api/mcp_remote/card_update_tools.py``, tested there by
``tests/unit/test_mcp_update_existing_cards.py``); these are the same promises
for this server, and every expected text is the hosted one.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_cli import card_types, card_updates, mcp_server, mcp_tools_platform, mcp_tools_projects
from beakr_cli.card_updates import REFUSALS, cards_retyped_text, fields_renamed_text
from tests import mcp_client

API = "https://api.test"
PROJECT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
TYPES = f"{API}/v1/knowledge/wiki/page-types"
RENAMED = {
    "page_type": "experiment",
    "dry_run": True,
    "cards": 40,
    "cards_changed": 31,
    "cards_written": 0,
    "cards_conflicted": 0,
    "cards_refused": 0,
    "cards_remaining": 0,
    "rows_renamed": 33,
    "rows_merged": 2,
    "applied": {"Run date": "Assay date"},
    "refused": {"Planned dose": "Dose"},
    "commit_id": None,
    "undo_event_id": None,
}
RETYPED = {
    "from_type": "assay",
    "to_type": "experiment",
    "dry_run": True,
    "cards": 12,
    "cards_retyped": 0,
    "cards_skipped": 0,
    "cards_remaining": 0,
    "commit_id": None,
    "undo_event_id": None,
}
UNDO = "0b1e8a64-0c41-4d5e-9b0a-1f2e3d4c5b6a"


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
    schema = listed["update_existing_cards"].input_schema
    assert set(schema["properties"]) == {"type", "project", "change", "renames", "to_type", "apply"}
    assert schema["required"] == ["type", "project", "change"]


@respx.mock
async def test_a_rename_without_apply_is_a_dry_run_and_says_nothing_changed() -> None:
    _project()
    route = respx.post(f"{TYPES}/experiment/rename-fields").respond(json=RENAMED)

    text = await mcp_tools_projects.update_existing_cards(
        "experiment",
        PROJECT,
        "rename_fields",
        renames=[{"old": "Run date", "new": "Assay date"}, {"old": " ", "new": "x"}],
    )

    assert json.loads(route.calls.last.request.content) == {
        "project_id": PROJECT,
        "renames": [{"old": "Run date", "new": "Assay date"}],
        "dry_run": True,
    }
    assert text == (
        "Preview only: nothing was changed.\n"
        "Renaming Run date to Assay date would change 31 of 40 experiment cards: "
        "33 rows renamed, 2 rows merged into a row that becomes the same field.\n"
        "Not accepted by the safety checks, so these would stay: Planned dose to Dose.\n"
        "Show the person these counts. If they agree, call this again with apply=true to "
        "make the change."
    )


@respx.mock
@pytest.mark.parametrize("apply", [None, False, "true", 1])
async def test_only_apply_true_writes(apply) -> None:
    # A truthy string or a number is not the person's agreement.
    _project()
    route = respx.post(f"{TYPES}/assay/retype-cards").respond(json=RETYPED)

    await mcp_tools_projects.update_existing_cards(
        "assay", PROJECT, "retype", to_type="experiment", apply=apply
    )

    assert json.loads(route.calls.last.request.content)["dry_run"] is True


@respx.mock
async def test_a_retype_with_apply_sends_the_write_to_one_encoded_path_segment() -> None:
    _project()
    route = respx.post(f"{TYPES}/Assay%20Run/retype-cards").respond(json=RETYPED)

    result = await mcp_client.call_tool(
        "update_existing_cards",
        {
            "type": "Assay Run",
            "scope": PROJECT,
            "change": "retype",
            "to_type": "experiment",
            "apply": True,
        },
    )

    assert not result.is_error, result.content[0].text
    assert route.calls.last.request.url.raw_path.endswith(b"/page-types/Assay%20Run/retype-cards")
    assert json.loads(route.calls.last.request.content) == {
        "project_id": PROJECT,
        "to_type": "experiment",
        "dry_run": False,
    }


@respx.mock
@pytest.mark.parametrize(
    ("args", "refusal"),
    [
        ({"type": " ", "change": "retype", "to_type": "x"}, "no_type"),
        ({"type": "a/b", "change": "retype", "to_type": "x"}, "bad_type"),
        ({"type": "../x", "change": "retype", "to_type": "x"}, "bad_type"),
        ({"type": "assay", "change": ""}, "no_change"),
        ({"type": "assay", "change": "delete"}, "no_change"),
        ({"type": "assay", "change": "rename_fields"}, "no_renames"),
        ({"type": "assay", "change": "rename_fields", "renames": [{}]}, "no_renames"),
        ({"type": "assay", "change": "retype"}, "no_to_type"),
        ({"type": "assay", "change": "retype", "to_type": "a?b"}, "bad_to_type"),
    ],
)
async def test_a_call_that_cannot_be_sent_is_refused_before_any_call(args, refusal) -> None:
    route = respx.route(url__startswith=API).respond(json={})

    result = await mcp_tools_projects.update_existing_cards(project=PROJECT, **args)

    message, fix = REFUSALS[refusal]
    assert result == {
        "status": "error",
        "error_code": "invalid_arguments",
        "message": message,
        "fix": fix,
    }
    assert not route.called


@respx.mock
async def test_no_project_is_refused_in_the_hosted_servers_words() -> None:
    route = respx.route(url__startswith=API).respond(json={})
    result = await mcp_tools_projects.update_existing_cards(
        "assay", " ", "retype", to_type="experiment"
    )
    assert (result["message"], result["fix"]) == card_types.NO_PROJECT
    assert not route.called


@respx.mock
async def test_a_mapping_is_read_as_the_same_renames() -> None:
    _project()
    route = respx.post(f"{TYPES}/experiment/rename-fields").respond(json=RENAMED)

    await mcp_tools_projects.update_existing_cards(
        "experiment", PROJECT, "rename_fields", renames={"Run date": "Assay date"}
    )

    assert json.loads(route.calls.last.request.content)["renames"] == [
        {"old": "Run date", "new": "Assay date"}
    ]


def test_an_applied_rename_names_what_was_skipped_and_how_to_undo_it() -> None:
    text = fields_renamed_text(
        RENAMED
        | {
            "dry_run": False,
            "cards_written": 28,
            "cards_conflicted": 2,
            "cards_refused": 1,
            "cards_remaining": 3,
            "commit_id": UNDO,
            "undo_event_id": UNDO,
        }
    )

    assert text == (
        "Renamed Run date to Assay date on 28 experiment cards.\n"
        "Not accepted by the safety checks, so these stay: Planned dose to Dose.\n"
        "2 cards changed while this ran and were left as they were.\n"
        "1 card could not be rewritten safely and waits for a person.\n"
        "3 cards still need the change. Running this again with apply=true picks up the "
        "cards that were skipped.\n"
        "It shows in the project's Activity, where a project owner can undo it."
    )


def test_a_rename_that_changes_nothing_says_so_and_offers_no_apply() -> None:
    nothing = RENAMED | {"cards_changed": 0, "rows_renamed": 0, "rows_merged": 0, "refused": {}}

    assert fields_renamed_text(nothing) == (
        "Preview only: nothing was changed.\nNo experiment card would change (40 cards read)."
    )
    assert fields_renamed_text(nothing | {"dry_run": False}) == "No experiment card was changed."


def test_a_preview_past_one_calls_bound_says_how_many_would_be_left() -> None:
    text = fields_renamed_text(
        RENAMED | {"cards_changed": 620, "cards": 700, "cards_remaining": 120}
    )

    assert (
        "More than 500 cards change, so applying works through them 500 at a time until none "
        "is left. It is still one change to undo."
    ) in text


def test_a_retype_preview_and_apply_read_as_what_happened() -> None:
    assert cards_retyped_text(RETYPED) == (
        "Preview only: nothing was changed.\n"
        "12 assay cards would become experiment cards.\n"
        "A card's text is not changed, so its rows keep their field names. If experiment "
        "names a field differently, rename fields on experiment afterwards.\n"
        "Show the person these counts. If they agree, call this again with apply=true to "
        "make the change."
    )
    assert cards_retyped_text(RETYPED | {"cards": 0}) == (
        "Preview only: nothing was changed.\nThis project has no assay cards to move."
    )
    assert cards_retyped_text(
        RETYPED
        | {
            "dry_run": False,
            "cards_retyped": 11,
            "cards_skipped": 1,
            "cards_remaining": 1,
            "commit_id": UNDO,
            "undo_event_id": UNDO,
        }
    ) == (
        "Moved 11 assay cards to experiment.\n"
        "1 card was being changed by something else and was skipped.\n"
        "1 assay card is left. Running this again with apply=true picks up the cards that "
        "were skipped.\n"
        "It shows in the project's Activity, where a project owner can undo it."
    )


def test_an_applied_change_whose_activity_row_was_not_written_names_the_change() -> None:
    text = cards_retyped_text(
        RETYPED | {"dry_run": False, "cards_retyped": 12, "commit_id": UNDO, "undo_event_id": None}
    )

    assert text.endswith(f"A project owner can undo it by reverting change {UNDO}.")


def test_the_commands_words_differ_from_the_tools_only_where_they_name_the_flag() -> None:
    tool = fields_renamed_text(RENAMED)
    command = fields_renamed_text(RENAMED, card_updates.COMMAND_WORDS)
    assert tool.splitlines()[:-1] == command.splitlines()[:-1]
    assert command.splitlines()[-1] == "Run this again with --apply to make the change."
