"""``beakr kb types settings|history|suggestions|dismiss-suggestion``, card counts on
``kb types ls``, ``--suggestion`` on ``kb types retype`` and the card type flags of
``beakr projects create``.

``settings`` shows a project's two settings, or previews a change and writes
nothing unless given ``--apply``. ``history`` and ``suggestions`` are one page a
call. Everything takes ``--json``.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
TYPES = f"{API}/v1/knowledge/wiki/page-types"
runner = CliRunner()
SETTINGS = {"inherit": "organization", "agent_may_change": True}
CHANGE = {
    "project_id": PROJECT,
    "dry_run": True,
    "current": SETTINGS,
    "requested": {"inherit": "own", "agent_may_change": True},
    "kept_types": ["index", "organization", "overview", "person", "topic"],
    "types_dropped": [
        {"key": "experiment", "label": "Experiment", "scope": "global", "live_cards": 12},
        {"key": "decision", "label": "Decision", "scope": "global", "live_cards": 0},
    ],
    "types_kept": [],
    "usable_own_types": ["compound"],
    "history_id": None,
    "undo_event_id": None,
    "suggestions": 0,
}
ENTRY = {
    "id": "0b1e8a64-0c41-4d5e-9b0a-1f2e3d4c5b6a",
    "actor_type": "user",
    "actor_user_id": "u1",
    "scope": "project",
    "key": "experiment",
    "label": "Experiment",
    "kind": "edited",
    "title": "Changed Experiment: description",
    "changed": ["description"],
    "undoable": True,
    "details": {},
    "created_at": "2026-11-20T10:00:00Z",
}
SUGGESTION = {
    **ENTRY,
    "id": "7c2d9e10-3f4a-4b5c-8d6e-9f0a1b2c3d4e",
    "actor_type": "agent",
    "actor_name": "Beakr",
    "kind": "retype_suggested",
    "key": "experiment",
    "title": "Move 12 Experiment cards to another type",
    "state": "pending",
    "details": {"from_type": "experiment", "cards": 12},
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.delenv("BEAKR_PROJECT_ID", raising=False)


def _invoke(*args: str, mode: str = "human", **kwargs):
    return runner.invoke(app, list(args), env={"BEAKR_OUTPUT": mode, "COLUMNS": "200"}, **kwargs)


@respx.mock
def test_settings_with_no_change_shows_the_two_settings() -> None:
    respx.get(TYPES).respond(200, json={"page_types": [], "map_mode": True, "settings": SETTINGS})
    shown = _invoke("kb", "types", "settings", "-P", PROJECT)
    assert shown.exit_code == 0, shown.output
    assert (
        "This project is offered the organization's card types and the built-in ones. "
        "Beakr may add and change its card types." in shown.output
    )
    piped = _invoke("kb", "types", "settings", "-P", PROJECT, mode="compact")
    assert piped.stdout.splitlines() == ["inherit\torganization", "agent_may_change\ttrue"]
    as_json = _invoke("kb", "types", "settings", "-P", PROJECT, "--json")
    assert json.loads(as_json.stdout) == SETTINGS


@respx.mock
@pytest.mark.parametrize("mode", ["human", "compact"])
def test_a_settings_change_previews_and_lists_what_would_drop_out(mode) -> None:
    # Root cause guarded: applying changes which types a whole project is
    # offered, so without --apply the command sends a dry run and says so.
    route = respx.put(f"{TYPES}/settings").respond(200, json=CHANGE)
    result = _invoke("kb", "types", "settings", "-P", PROJECT, "--inherit", "own", mode=mode)
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "project_id": PROJECT,
        "inherit": "own",
        "keep_types_in_use": False,
        "dry_run": True,
    }
    assert result.stdout.splitlines() == [
        "Preview only: nothing was changed.",
        "This project would be offered only its own card types.",
        "It keeps these built-in types: index, organization, overview, person, topic.",
        "Would no longer be offered in this project: Experiment (experiment, 12 cards); "
        "Decision (decision, no cards).",
        "Their cards are not changed and stay readable; switching back offers the types again.",
        "To keep the types that have cards as the project's own types, pass --keep-types-in-use.",
        "Run this again with --apply to make the change.",
    ]


@respx.mock
def test_a_settings_change_applies_only_with_apply() -> None:
    applied = CHANGE | {
        "dry_run": False,
        "requested": {"inherit": "own", "agent_may_change": False},
        "suggestions": 1,
        "history_id": "h1",
    }
    route = respx.put(f"{TYPES}/settings").respond(200, json=applied)
    args = ("kb", "types", "settings", "-P", PROJECT, "--inherit", "own", "--no-beakr-may-add")
    result = _invoke(*args, "--keep-types-in-use", "--apply", mode="compact")
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "project_id": PROJECT,
        "inherit": "own",
        "agent_may_change": False,
        "keep_types_in_use": True,
        "dry_run": False,
    }
    lines = result.stdout.splitlines()
    assert lines[0] == "This project is now offered only its own card types."
    assert "Beakr no longer adds or changes this project's card types; only people do." in lines
    assert lines[-1] == "It shows in the project's Activity and in its card type history."
    as_json = _invoke(*args, "--apply", "--json")
    assert json.loads(as_json.stdout) == applied


@respx.mock
def test_settings_refuses_a_bad_choice_and_flags_with_nothing_to_change() -> None:
    route = respx.route(url__startswith=API).respond(200, json={})
    for extra in (["--inherit", "all"], ["--apply"], ["--keep-types-in-use"]):
        result = _invoke("kb", "types", "settings", "-P", PROJECT, *extra)
        assert result.exit_code == 2, extra
    assert not route.called


@respx.mock
def test_history_lists_a_projects_changes_and_the_organizations() -> None:
    route = respx.get(f"{TYPES}/history").respond(
        200, json={"data": [ENTRY], "has_more": True, "next_cursor": "c2"}
    )
    shown = _invoke("kb", "types", "history", "-P", PROJECT, "--key", "experiment")
    assert shown.exit_code == 0, shown.output
    assert dict(route.calls.last.request.url.params) == {
        "project_id": PROJECT,
        "key": "experiment",
        "limit": "50",
    }
    assert "Changed Experiment: description" in shown.output and "edited" in shown.output
    assert "--cursor c2" in shown.output
    piped = _invoke(
        "kb", "types", "history", "--org-wide", "--suggestions", "-n", "5", mode="compact"
    )
    assert dict(route.calls.last.request.url.params) == {
        "scope": "org",
        "include_suggestions": "true",
        "limit": "5",
    }
    assert piped.stdout.splitlines()[0] == (
        f"{ENTRY['id']}\t2026-11-20T10:00:00Z\tedited\texperiment\tChanged Experiment: description"
    )
    refused = _invoke("kb", "types", "history", "--org-wide", "-P", PROJECT)
    assert refused.exit_code == 2
    as_json = _invoke("kb", "types", "history", "-P", PROJECT, "--cursor", "c2", "--json")
    assert route.calls.last.request.url.params["cursor"] == "c2"
    assert json.loads(as_json.stdout)["data"] == [ENTRY]


@respx.mock
def test_suggestions_say_how_to_answer_each_and_one_is_dismissed() -> None:
    held = {**SUGGESTION, "id": "h-1", "kind": "held_back", "title": "Beakr wanted to add Assay"}
    listing = respx.get(f"{TYPES}/suggestions").respond(
        200, json={"data": [SUGGESTION, held], "has_more": False, "next_cursor": None}
    )
    shown = _invoke("kb", "types", "suggestions", "-P", PROJECT)
    assert shown.exit_code == 0, shown.output
    assert dict(listing.calls.last.request.url.params) == {"project_id": PROJECT, "limit": "50"}
    assert (
        f"beakr kb types retype 'experiment' -P {PROJECT} --to TYPE "
        f"--suggestion {SUGGESTION['id']}" in shown.output
    )
    assert f"beakr kb types dismiss-suggestion h-1 -P {PROJECT}" in shown.output
    piped = _invoke("kb", "types", "suggestions", "-P", PROJECT, mode="compact")
    assert piped.stdout.splitlines()[0].endswith("state=pending")

    dismiss = respx.post(f"{TYPES}/suggestions/{SUGGESTION['id']}/dismiss").respond(
        200, json={"suggestion": {**SUGGESTION, "state": "dismissed"}, "activity_event_id": "e1"}
    )
    done = _invoke("kb", "types", "dismiss-suggestion", SUGGESTION["id"], "-P", PROJECT)
    assert done.exit_code == 0, done.output
    assert json.loads(dismiss.calls.last.request.content) == {"project_id": PROJECT}
    assert "Dismissed the suggestion: Move 12 Experiment cards to another type" in done.output


@respx.mock
def test_dismissing_an_answered_suggestion_is_a_conflict() -> None:
    respx.post(f"{TYPES}/suggestions/sg1/dismiss").respond(
        409, json={"error": {"code": "conflict", "message": "It was already answered."}}
    )
    result = _invoke("kb", "types", "dismiss-suggestion", "sg1", "-P", PROJECT)
    assert result.exit_code == 5
    assert "already answered" in result.output


@respx.mock
def test_ls_shows_the_setting_in_force_and_card_counts_on_request() -> None:
    listing = {
        "map_mode": True,
        "page_types": [
            {"key": "experiment", "label": "Experiment", "status": "active", "scope": "global"}
        ],
        "settings": {"inherit": "builtin", "agent_may_change": False},
        "kept_types": [],
    }
    route = respx.get(TYPES).respond(200, json=listing)
    plain = _invoke("kb", "types", "ls", "-P", PROJECT)
    assert dict(route.calls.last.request.url.params) == {"project_id": PROJECT}
    assert (
        "This project is offered the built-in card types only. "
        "Only people add and change its card types." in plain.output
    )
    assert "Cards" not in plain.output

    counted = dict(listing)
    counted["page_types"] = [{**listing["page_types"][0], "card_count": 7}]
    counted["unlisted_types"] = [{"key": "matter", "card_count": 2}]
    route.respond(200, json=counted)
    shown = _invoke("kb", "types", "ls", "-P", PROJECT, "--counts")
    assert dict(route.calls.last.request.url.params) == {
        "project_id": PROJECT,
        "include_counts": "true",
    }
    assert "Cards" in shown.output and "matter (2)" in shown.output
    piped = _invoke("kb", "types", "ls", "-P", PROJECT, "-c", mode="compact")
    assert piped.stdout.splitlines() == [
        "experiment\tExperiment\tscope=built-in\tstatus=active\tcards=7",
        "unlisted\tmatter\tcards=2",
        "settings\tinherit=builtin\tagent_may_change=false",
    ]


@respx.mock
def test_retype_names_the_suggestion_it_answers() -> None:
    route = respx.post(f"{TYPES}/experiment/retype-cards").respond(
        200, json={"from_type": "experiment", "to_type": "assay", "dry_run": True, "cards": 12}
    )
    result = _invoke(
        "kb", "types", "retype", "experiment", "-P", PROJECT, "--to", "assay", "--suggestion", "s1"
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "project_id": PROJECT,
        "to_type": "assay",
        "dry_run": True,
        "suggestion_id": "s1",
    }


@respx.mock
def test_projects_create_takes_the_card_type_choice() -> None:
    route = respx.post(f"{API}/v1/projects").respond(201, json={"id": OTHER, "name": "Lyon"})
    result = _invoke(
        "projects",
        "create",
        "Lyon",
        "--card-types",
        "own",
        "--no-beakr-may-add",
        "--copy-types-from",
        PROJECT,
        "--copy-type",
        "experiment",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "name": "Lyon",
        "card_types": {
            "inherit": "own",
            "agent_may_change": False,
            "copy_from_project_id": PROJECT,
            "copy_keys": ["experiment"],
        },
    }
    plain = _invoke("projects", "create", "Basel")
    assert plain.exit_code == 0, plain.output
    assert json.loads(route.calls.last.request.content) == {"name": "Basel"}
    assert route.call_count == 2
    for extra in (["--card-types", "all"], ["--card-types", "own"], ["--copy-type", "x"]):
        refused = _invoke("projects", "create", "Lyon", *extra)
        assert refused.exit_code == 2, extra
    assert route.call_count == 2
