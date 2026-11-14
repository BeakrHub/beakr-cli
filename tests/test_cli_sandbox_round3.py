"""CLI options and outputs the sandbox run of 2026-11-14 found missing or wrong.

Each test names its root cause:

* ``kb sources`` read the REST sources route, whose records carry
  ``retrieval: null``: a connected item printed a url and no read call, and a
  file ``beakr files download`` instead of a read. It is now the door's
  ``sources``, rendered from the reply's source records as ``kb cat`` is.
* ``kb sections --sources`` answered "Did you mean --scope?": there was no way
  to drop or shorten the Sources list.
* ``kb grep`` had no ``files_with_matches``, ``count`` or title target.
* ``kb proposals ls`` printed an 8-character id that ``show``, ``accept`` and
  ``dismiss`` did not take.
* ``kb proposals show`` printed stored text, ``<!-- sec:... -->`` markers and
  ``{{conversation:...}}`` tokens included.
* ``beakr version`` and ``beakr context`` did not say which Beakr and which
  organization they talked to, as the MCP tools do.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app
from tests._shell_commands import parse_command

API = "https://api.test"
PROJECT = "9135d3be-d08e-4fa4-af6c-de78d128521b"
PAGE = "85e52f3b-4255-4471-848f-1b9eb0832196"
FILE_ID = "2e0f2ad2-dd4f-41ae-ad2f-a890db6c8934"
COMMAND = f"{API}/v1/knowledge/wiki/command"
PROPOSALS = f"{API}/v1/knowledge/wiki/proposals"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("COLUMNS", "400")


@pytest.fixture
def terminal(monkeypatch):
    """Render for a person: CliRunner's stdout is not a TTY, which means JSON."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _sent(route: respx.Route, index: int = -1) -> dict[str, Any]:
    return json.loads(route.calls[index].request.content)


def _page() -> None:
    page = {"id": PAGE, "title": "Beakr's Lightfield Integration", "project_id": PROJECT}
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE}").mock(return_value=Response(200, json=page))


# --- kb sources -----------------------------------------------------------------------

#: The door's `sources` text for the Lightfield page, as recorded on sandbox.
SOURCES_TEXT = (
    f"KB page ID: {PAGE}\n"
    "1 distinct source(s) with 2 cited passage(s), for Beakr's Lightfield Integration:\n\n"
    "  - Slack: #lightfield-beakr (external_item:9590d90d, origin: slack_channel)\n"
    "     url: https://slack.com/app_redirect?channel=C0AMUSP0YMB\n"
    "     fetch: integrations command='call' tool='slack_list_messages' "
    'arguments={"channel_id": "C0AMUSP0YMB"}\n'
    "  - WEIGHTED_RESPONSE_CHECKPOINT_2026-10-28.md (artifact:2e0f2ad2)\n"
    f"     read: read_file with artifact_id={FILE_ID}\n"
)
SOURCES_RECORDS = [
    {
        "source_ref": "beakr-source:v1:153be0b4-980b-4140-b9d5-f5b2ee22c761",
        "name": "Slack: #lightfield-beakr",
        "provider": "slack",
        "location": {"url": "https://slack.com/app_redirect?channel=C0AMUSP0YMB"},
        "retrieval": {"tool": "slack_list_messages", "arguments": {"channel_id": "C0AMUSP0YMB"}},
    },
    {
        "source_ref": "beakr-source:v1:4a3f5054-d8ff-427b-b33e-704e64d49f8c",
        "name": "WEIGHTED_RESPONSE_CHECKPOINT_2026-10-28.md",
        "location": {"file_id": FILE_ID},
    },
]


@respx.mock
def test_kb_sources_prints_the_read_call_of_each_source_from_its_record():
    _page()
    door = respx.post(COMMAND).mock(
        return_value=Response(
            200,
            json={
                "content": [{"type": "text", "text": SOURCES_TEXT}],
                "outcome": {"status": "success"},
                "sources": SOURCES_RECORDS,
            },
        )
    )

    result = runner.invoke(app, ["kb", "sources", PAGE, "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert _sent(door)["command"] == "sources"
    out = result.stdout
    slack = 'beakr integrations call slack_list_messages --args \'{"channel_id": "C0AMUSP0YMB"}\''
    assert f"     fetch: {slack}" in out
    assert f"     read: beakr documents read {FILE_ID}" in out
    assert "beakr files download" not in out
    for line in out.splitlines():
        if "fetch: beakr " in line or "read: beakr " in line:
            parse_command(line.split(": ", 1)[1])


@respx.mock
def test_kb_sources_json_is_the_doors_reply_and_paging_flags_reach_the_door():
    _page()
    reply = {
        "content": [{"type": "text", "text": SOURCES_TEXT}],
        "outcome": {"status": "success"},
        "coverage": {
            "complete": False,
            "continue_with": {"after_title": "W", "after_source_id": "s"},
        },
        "sources": SOURCES_RECORDS,
    }
    door = respx.post(COMMAND).mock(return_value=Response(200, json=reply))

    result = runner.invoke(
        app,
        ["kb", "sources", PAGE, "-n", "5", "--after-title", "M", "--after-source-id", "s-1"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(runner.invoke(app, ["kb", "sources", PAGE, "--json"]).stdout) == reply
    sent = _sent(door, 0)
    assert (sent["limit"], sent["after_title"], sent["after_source_id"]) == (5, "M", "s-1")
    # The continuation is this command with the reply's cursor.
    assert f"`beakr kb sources {PAGE} --limit 5 --after-title W --after-source-id s`" in (
        result.stdout
    )


# --- kb sections --sources --------------------------------------------------------------

_HIT = "1 section result(s):\n\n1. [[Beakr]] › Product"


def _sections_reply() -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": _HIT}],
        "outcome": {"status": "success"},
        "sources": SOURCES_RECORDS,
    }


@respx.mock
def test_kb_sections_sources_none_drops_the_list_and_the_doors_legend():
    door = respx.post(COMMAND).mock(return_value=Response(200, json=_sections_reply()))

    result = runner.invoke(app, ["kb", "sections", "onboarding", "--sources", "none"])

    assert result.exit_code == 0, result.output
    assert _sent(door)["include_sources"] is False
    assert "Sources (" not in result.stdout and "slack_list_messages" not in result.stdout


@respx.mock
@pytest.mark.parametrize("mode", ["brief", "full", None])
def test_kb_sections_sources_brief_and_full(mode):
    door = respx.post(COMMAND).mock(return_value=Response(200, json=_sections_reply()))
    flags = ["--sources", mode] if mode else []

    result = runner.invoke(app, ["kb", "sections", "onboarding", *flags])

    assert result.exit_code == 0, result.output
    assert "include_sources" not in _sent(door)
    assert "Sources (" in result.stdout
    if mode == "brief":
        assert "(--sources full adds each document's excerpt, url and file id)" in result.stdout
    else:
        # Full, as it always printed: each document with its own lines.
        assert "--sources full adds" not in result.stdout


def test_kb_sections_sources_takes_only_its_three_values():
    result = runner.invoke(app, ["kb", "sections", "onboarding", "--sources", "all"])
    assert result.exit_code == 2


# --- kb grep modes ----------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("flags", "sent"),
    [
        (["-l"], {"output_mode": "files_with_matches"}),
        (["--files-with-matches"], {"output_mode": "files_with_matches"}),
        (["-c"], {"output_mode": "count"}),
        (["--count"], {"output_mode": "count"}),
        (["--title"], {"target": "title"}),
        (["-l", "--title"], {"output_mode": "files_with_matches", "target": "title"}),
    ],
)
def test_kb_grep_output_modes_and_title_target(flags, sent):
    door = respx.post(COMMAND).mock(
        return_value=Response(
            200, json={"content": [{"type": "text", "text": "2 pages"}], "outcome": {}}
        )
    )

    result = runner.invoke(app, ["kb", "grep", "Lightfield", *flags])

    assert result.exit_code == 0, result.output
    body = _sent(door)
    assert {key: body.get(key) for key in sent} == sent


def test_kb_grep_files_with_matches_and_count_are_exclusive():
    assert runner.invoke(app, ["kb", "grep", "x", "-l", "-c"]).exit_code == 2


# --- kb proposals: short ids ------------------------------------------------------------

ONE = "e5632ad8-9658-406f-b12c-e204da797e0b"
TWO = "e5632ad8-1111-4111-8111-111111111111"
THREE = "16b69089-8516-4984-82c2-c6e50694dae1"


def _listing(*ids: str) -> dict[str, Any]:
    return {
        "data": [
            {"id": pid, "proposal_type": "edit", "status": "pending", "page_title": "Card"}
            for pid in ids
        ],
        "has_more": False,
        "next_cursor": None,
    }


@respx.mock
def test_proposals_ls_shows_the_short_id_that_show_takes(terminal):
    respx.get(PROPOSALS).mock(return_value=Response(200, json=_listing(ONE, THREE)))

    result = runner.invoke(app, ["kb", "proposals", "ls"])

    assert result.exit_code == 0, result.output
    assert "e5632ad8" in result.stdout and "16b69089" in result.stdout


@respx.mock
def test_proposals_show_accepts_a_unique_id_prefix():
    respx.get(PROPOSALS).mock(return_value=Response(200, json=_listing(ONE, THREE)))
    shown = respx.get(f"{PROPOSALS}/{THREE}").mock(
        return_value=Response(200, json={"id": THREE, "proposal_type": "edit", "status": "pending"})
    )

    result = runner.invoke(app, ["kb", "proposals", "show", "16b69089", "--json"])

    assert result.exit_code == 0, result.output
    assert shown.called


@respx.mock
@pytest.mark.parametrize("verb", ["show", "accept", "dismiss"])
def test_an_ambiguous_prefix_is_refused_with_each_candidate(verb):
    respx.get(PROPOSALS).mock(return_value=Response(200, json=_listing(ONE, TWO)))
    acted = respx.post(url__regex=rf"{PROPOSALS}/.*").mock(return_value=Response(200, json={}))

    yes = [] if verb == "show" else ["--yes"]
    result = runner.invoke(app, ["kb", "proposals", verb, "e5632ad8", *yes])

    assert result.exit_code == 2, result.output
    assert ONE in result.output and TWO in result.output
    assert not acted.called


@respx.mock
def test_accept_and_dismiss_take_a_unique_prefix_of_a_pending_proposal():
    listed = respx.get(PROPOSALS).mock(return_value=Response(200, json=_listing(ONE, THREE)))
    dismissed = respx.post(f"{PROPOSALS}/{ONE}/dismiss").mock(
        return_value=Response(200, json={"proposal_id": ONE, "status": "dismissed"})
    )

    result = runner.invoke(app, ["kb", "proposals", "dismiss", "e563", "--yes"])

    assert result.exit_code == 0, result.output
    assert dismissed.called
    assert listed.calls[0].request.url.params["status"] == "pending"


def test_a_prefix_too_short_to_name_a_proposal_is_refused():
    assert runner.invoke(app, ["kb", "proposals", "show", "e56"]).exit_code == 2


@respx.mock
def test_a_whole_id_is_used_without_listing():
    listed = respx.get(PROPOSALS).mock(return_value=Response(200, json=_listing()))
    respx.get(f"{PROPOSALS}/{ONE}").mock(
        return_value=Response(200, json={"id": ONE, "status": "pending"})
    )
    assert runner.invoke(app, ["kb", "proposals", "show", ONE, "--json"]).exit_code == 0
    assert not listed.called


# --- kb proposals show: readable page text ------------------------------------------------

STORED = (
    "<!-- sec:sec_description -->\n## Description\n\nThis is labelled test content. "
    "{{conversation:sbx-cli-check-local-mcp}} It can be archived at any time."
)
PROPOSAL = {
    "id": ONE,
    "proposal_type": "create",
    "status": "accepted",
    "page_title": "Sandbox CLI check 2026-11-14 (local MCP)",
    "payload": {
        "title": "Sandbox CLI check 2026-11-14 (local MCP)",
        "content": STORED,
        "sections": [
            {
                "id": "sec_description",
                "body": STORED.split("\n\n", 1)[1],
                "citations": [
                    {
                        "key": "conversation:sbx-cli-check-local-mcp",
                        "source_type": "conversation",
                        "source_title": "Sandbox CLI verification session, 2026-11-14",
                    }
                ],
            }
        ],
    },
}


@respx.mock
@pytest.mark.parametrize("piped", [False, True], ids=["terminal", "piped"])
def test_proposals_show_prints_page_text_as_a_read_does(piped, monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: piped)
    respx.get(f"{PROPOSALS}/{ONE}").mock(return_value=Response(200, json=PROPOSAL))

    out = runner.invoke(app, ["kb", "proposals", "show", ONE]).stdout

    assert "<!-- sec:" not in out and "{{" not in out
    assert (
        "This is labelled test content. (Sandbox CLI verification session, 2026-11-14) "
        "It can be archived at any time." in out
    )
    # The citation's own key is still listed among its citations.
    assert "conversation:sbx-cli-check-local-mcp" in out


@respx.mock
def test_proposals_show_raw_and_json_keep_the_stored_text():
    respx.get(f"{PROPOSALS}/{ONE}").mock(return_value=Response(200, json=PROPOSAL))

    raw = runner.invoke(app, ["kb", "proposals", "show", ONE, "--raw"]).stdout
    as_json = json.loads(runner.invoke(app, ["kb", "proposals", "show", ONE, "--json"]).stdout)

    assert "<!-- sec:sec_description -->" in raw
    assert "{{conversation:sbx-cli-check-local-mcp}}" in raw
    assert as_json == PROPOSAL


# --- version and context name where the calls land -------------------------------------

WORKSPACE = {"environment": "sandbox", "organization": {"name": "Beakr", "slug": "beakr"}}
CONNECTED = 'Connected to: Beakr sandbox, organization "Beakr" (slug beakr).'


@respx.mock
def test_version_says_which_beakr_and_organization_it_talks_to(monkeypatch):
    monkeypatch.setenv("BEAKR_NO_UPDATE_CHECK", "1")
    respx.get(f"{API}/v1/organizations/me/active").mock(
        return_value=Response(200, json={"id": "o", "name": "Beakr", "slug": "beakr"})
    )
    respx.get(f"{API}/v1/me/organization").mock(
        return_value=Response(200, json={"id": "o", "name": "Beakr", "slug": "beakr"})
    )
    respx.post(f"{API}/v1/context").mock(
        return_value=Response(200, json={"content": "", "workspace": WORKSPACE})
    )

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    first, second = result.stdout.splitlines()[:2]
    assert first.startswith("beakr-cli ")
    assert second == CONNECTED
    assert json.loads(runner.invoke(app, ["version", "--json"]).stdout)["connected"] == CONNECTED


def test_version_offline_or_signed_out_asks_nothing(monkeypatch):
    monkeypatch.delenv("BEAKR_API_KEY")
    result = runner.invoke(app, ["version", "--offline"])
    assert result.exit_code == 0
    assert "Connected to" not in result.stdout


@respx.mock
def test_context_opens_with_the_connected_line():
    respx.post(f"{API}/v1/context").mock(
        return_value=Response(200, json={"content": "# Context\n", "workspace": WORKSPACE})
    )

    result = runner.invoke(app, ["context", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"{CONNECTED}\n\n# Context")
