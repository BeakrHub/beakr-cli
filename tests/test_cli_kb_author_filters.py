"""``beakr kb log``, ``kb proposals ls`` and ``kb ls`` filtered by who wrote a change.

The REST routes these commands read take no author filter, so a command given
one reads the Knowledge door (``POST /v1/knowledge/wiki/command``) with the
door's own argument names. Without a filter each command must keep the request
and the output it had.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
PAGE_ID = "33333333-3333-3333-3333-333333333333"
PROJECT = "22222222-2222-2222-2222-222222222222"
PAGES = f"{API}/v1/knowledge/wiki/pages"
PROPOSALS = f"{API}/v1/knowledge/wiki/proposals"
COMMAND = f"{API}/v1/knowledge/wiki/command"
runner = CliRunner()

PAGE = {"id": PAGE_ID, "title": "Onboarding", "page_type": "process", "project_id": PROJECT}
HISTORY_FLAGS = [
    "--by",
    "Ali Chen",
    "--via",
    "codex",
    "--kind",
    "agent",
    "--accepted-by",
    "auto-approve",
    "--since",
    "2026-11-09",
    "--until",
    "2026-11-13",
    "--offset",
    "20",
]
HISTORY_ARGUMENTS = {
    "by": "Ali Chen",
    "via": "codex",
    "kind": "agent",
    "accepted_by": "auto-approve",
    "since": "2026-11-09",
    "until": "2026-11-13",
    "offset": 20,
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _door(text: str, **extra: object) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "outcome": {"status": "success"},
        **extra,
    }


def _envelope(rows: list[dict]) -> dict:
    return {"data": rows, "next_cursor": None, "has_more": False}


def _sent(route: respx.Route) -> dict:
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------------------------
# kb log
# ---------------------------------------------------------------------------


@respx.mock
def test_log_filters_reach_the_door_under_its_argument_names():
    door = respx.post(COMMAND).respond(json=_door("Filters: by Ali Chen.\nrev 4  Launch Plan"))
    result = runner.invoke(app, ["kb", "log", "--project", PROJECT, *HISTORY_FLAGS])
    assert result.exit_code == 0, result.output
    assert _sent(door) == {"command": "log", "scope": PROJECT, **HISTORY_ARGUMENTS}
    # The door's text, as the other door commands print it.
    assert "Filters: by Ali Chen." in result.stdout
    assert "rev 4  Launch Plan" in result.stdout


@respx.mock
def test_log_of_one_page_with_a_filter_reads_the_door_not_the_revisions_route():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    revisions = respx.get(f"{PAGES}/{PAGE_ID}/revisions").respond(json=_envelope([]))
    door = respx.post(COMMAND).respond(json=_door("rev 2  Ali Chen via codex"))
    result = runner.invoke(app, ["kb", "log", PAGE_ID, "--project", PROJECT, "--via", "codex"])
    assert result.exit_code == 0, result.output
    assert not revisions.called
    assert _sent(door) == {"command": "log", "scope": PROJECT, "page": PAGE_ID, "via": "codex"}


@respx.mock
def test_log_of_one_page_without_a_filter_still_reads_the_revisions_route():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    revision = {"revision": 3, "compiled_by": "human:1", "summary": "Edit", "created_at": "2026"}
    revisions = respx.get(f"{PAGES}/{PAGE_ID}/revisions").respond(json=_envelope([revision]))
    door = respx.post(COMMAND).respond(json=_door("unused"))
    result = runner.invoke(app, ["kb", "log", PAGE_ID, "--project", PROJECT, "--json"])
    assert result.exit_code == 0, result.output
    assert revisions.called
    assert not door.called
    assert json.loads(result.stdout) == [revision]


@respx.mock
def test_log_without_a_page_or_filter_sends_the_door_no_filter_argument():
    door = respx.post(COMMAND).respond(json=_door("rev 4  Launch Plan"))
    result = runner.invoke(app, ["kb", "log", "--project", PROJECT])
    assert result.exit_code == 0, result.output
    assert _sent(door) == {"command": "log", "scope": PROJECT}


@respx.mock
def test_a_filtered_log_as_json_is_the_doors_reply_with_its_coverage():
    reply = _door(
        "Filters: via codex.",
        coverage={"returned": 20, "complete": False, "next_offset": 20},
        next=[{"command": "log", "arguments": {"via": "codex", "offset": 20}}],
    )
    respx.post(COMMAND).respond(json=reply)
    result = runner.invoke(app, ["kb", "log", "--project", PROJECT, "--via", "codex", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == reply


@respx.mock
def test_a_refused_filter_prints_the_doors_reason_and_fails():
    reply = {
        "content": [{"type": "text", "text": "log: more than one person matches 'Ali'."}],
        "outcome": {"status": "error", "error_code": "person_ambiguous"},
    }
    respx.post(COMMAND).respond(json=reply)
    result = runner.invoke(app, ["kb", "log", "--project", PROJECT, "--by", "Ali"])
    assert result.exit_code == 1
    assert "more than one person matches" in result.stdout


# ---------------------------------------------------------------------------
# kb proposals ls
# ---------------------------------------------------------------------------


@respx.mock
def test_proposals_filters_reach_the_door_with_status_and_limit():
    listing = respx.get(PROPOSALS).respond(json=_envelope([]))
    door = respx.post(COMMAND).respond(json=_door("Filters: filed by Ali Chen.\n1 proposal"))
    result = runner.invoke(
        app,
        [
            "kb",
            "proposals",
            "ls",
            "--project",
            PROJECT,
            "--status",
            "accepted",
            "--limit",
            "5",
            *HISTORY_FLAGS,
        ],
    )
    assert result.exit_code == 0, result.output
    assert not listing.called
    assert _sent(door) == {
        "command": "proposals",
        "scope": PROJECT,
        "status": "accepted",
        "limit": 5,
        **HISTORY_ARGUMENTS,
    }
    assert "Filters: filed by Ali Chen." in result.stdout


@respx.mock
def test_filtered_proposals_without_a_project_are_not_scoped_like_the_unfiltered_listing():
    # The unfiltered listing names no project unless one is passed; the filtered
    # one must cover the same proposals, not the directory's default project.
    door = respx.post(COMMAND).respond(json=_door("No matching proposals."))
    result = runner.invoke(app, ["kb", "proposals", "ls", "--via", "codex"])
    assert result.exit_code == 0, result.output
    assert _sent(door) == {
        "command": "proposals",
        "status": "pending",
        "limit": 50,
        "via": "codex",
    }


@respx.mock
def test_proposals_without_a_filter_still_read_the_list_route():
    listing = respx.get(PROPOSALS).respond(json=_envelope([]))
    door = respx.post(COMMAND).respond(json=_door("unused"))
    result = runner.invoke(app, ["kb", "proposals", "ls", "--project", PROJECT, "--json"])
    assert result.exit_code == 0, result.output
    assert not door.called
    params = listing.calls[0].request.url.params
    assert dict(params) == {"status": "pending", "limit": "50", "project_id": PROJECT}
    assert json.loads(result.stdout) == []


@respx.mock
def test_filtered_proposals_refuse_a_cursor():
    door = respx.post(COMMAND).respond(json=_door("unused"))
    result = runner.invoke(app, ["kb", "proposals", "ls", "--by", "me", "--cursor", "abc"])
    assert result.exit_code == 2
    assert "--offset" in result.output
    assert not door.called


# ---------------------------------------------------------------------------
# kb ls
# ---------------------------------------------------------------------------


@respx.mock
def test_ls_filters_reach_the_door_under_its_argument_names():
    pages = respx.get(PAGES).respond(json=_envelope([]))
    door = respx.post(COMMAND).respond(json=_door("1 matching page(s):\nFilters: created by Ali."))
    result = runner.invoke(
        app,
        [
            "kb",
            "ls",
            "--project",
            PROJECT,
            "--type",
            "decision",
            "--limit",
            "40",
            "--after-title",
            "Launch Plan",
            "--created-by",
            "Ali Chen",
            "--created-via",
            "claude-code",
            "--created-since",
            "2026-11-01",
            "--changed-by",
            "me",
            "--changed-via",
            "codex",
            "--changed-since",
            "2026-11-09T14:00:00-05:00",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not pages.called
    assert _sent(door) == {
        "command": "ls",
        "scope": PROJECT,
        "page_type": "decision",
        "limit": 40,
        "after_title": "Launch Plan",
        "created_by": "Ali Chen",
        "created_via": "claude-code",
        "created_since": "2026-11-01",
        "changed_by": "me",
        "changed_via": "codex",
        "changed_since": "2026-11-09T14:00:00-05:00",
    }
    assert "Filters: created by Ali." in result.stdout


@respx.mock
def test_filtered_ls_under_a_parent_sends_the_parents_id():
    respx.get(f"{PAGES}/by-title").respond(json=PAGE)
    door = respx.post(COMMAND).respond(json=_door("No matching pages."))
    result = runner.invoke(
        app, ["kb", "ls", "--project", PROJECT, "--parent", "Onboarding", "--changed-via", "codex"]
    )
    assert result.exit_code == 0, result.output
    assert _sent(door) == {
        "command": "ls",
        "scope": PROJECT,
        "parent": PAGE_ID,
        "changed_via": "codex",
    }


@respx.mock
def test_filtered_ls_across_all_scopes_names_no_project():
    door = respx.post(COMMAND).respond(json=_door("No matching pages."))
    result = runner.invoke(app, ["kb", "ls", "--project", PROJECT, "--all", "--changed-by", "me"])
    assert result.exit_code == 0, result.output
    assert _sent(door) == {"command": "ls", "changed_by": "me"}


@respx.mock
def test_ls_without_a_filter_still_reads_the_pages_route():
    pages = respx.get(PAGES).respond(json=_envelope([PAGE]))
    door = respx.post(COMMAND).respond(json=_door("unused"))
    result = runner.invoke(app, ["kb", "ls", "--project", PROJECT, "--json"])
    assert result.exit_code == 0, result.output
    assert not door.called
    assert pages.calls[0].request.url.params["project_id"] == PROJECT
    assert json.loads(result.stdout) == [PAGE]


@pytest.mark.parametrize("flags", [["--roots"], ["--cursor", "abc"], ["--json", "--envelope"]])
@respx.mock
def test_filtered_ls_refuses_the_options_only_the_pages_route_has(flags):
    door = respx.post(COMMAND).respond(json=_door("unused"))
    result = runner.invoke(app, ["kb", "ls", "--project", PROJECT, "--created-by", "me", *flags])
    assert result.exit_code == 2
    assert "author filter" in result.output
    assert not door.called


@respx.mock
def test_after_title_without_a_filter_is_a_usage_error():
    pages = respx.get(PAGES).respond(json=_envelope([]))
    result = runner.invoke(app, ["kb", "ls", "--project", PROJECT, "--after-title", "Launch"])
    assert result.exit_code == 2
    assert "--cursor" in result.output
    assert not pages.called
