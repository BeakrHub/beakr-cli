"""``beakr kb cat``, ``kb provenance`` and ``kb proposals ls|show|accept|dismiss``
against a mocked API: the requests each sends and what each prints."""

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
runner = CliRunner()

PAGE = {
    "id": PAGE_ID,
    "title": "Onboarding",
    "page_type": "process",
    "revision": 3,
    "project_id": PROJECT,
    "content": "# Onboarding\n\nNew hires pair for a week.",
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def terminal(monkeypatch):
    """Render for a person: CliRunner's stdout is not a TTY, which means JSON."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _envelope(rows: list[dict]) -> dict:
    return {"data": rows, "next_cursor": None, "has_more": False}


# ---------------------------------------------------------------------------
# kb cat
# ---------------------------------------------------------------------------


@respx.mock
def test_cat_by_id_prints_the_page_as_json():
    route = respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    respx.get(f"{PAGES}/{PAGE_ID}/sources").respond(json=_envelope([]))
    result = runner.invoke(app, ["kb", "cat", PAGE_ID, "--json"])
    assert result.exit_code == 0, result.output
    assert route.called
    # The page, plus where each of its sources lives (none here).
    assert json.loads(result.stdout) == {**PAGE, "sources": []}


@respx.mock
def test_cat_by_title_resolves_through_by_title(terminal):
    route = respx.get(f"{PAGES}/by-title").respond(json=PAGE)
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(
        json={
            "content": [{"type": "text", "text": "# Onboarding\nRev: 3\n\nNew hires pair."}],
            "outcome": {"status": "success"},
        }
    )
    result = runner.invoke(app, ["kb", "cat", "Onboarding"])
    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["title"] == "Onboarding"
    assert json.loads(door.calls.last.request.content)["page"] == PAGE_ID
    assert "Rev: 3" in result.stdout
    assert "New hires pair." in result.stdout


# Root cause: the default `kb cat` printed the stored markdown, whose citations
# are raw {{artifact:...}} tokens, while `--rev` read the door's `cat`, which
# renders each as the reference a reader can follow. Every text read now goes
# through the door; --json keeps the stored page record.
@respx.mock
@pytest.mark.parametrize("as_terminal", [True, False], ids=["terminal", "piped"])
def test_cat_prints_readable_references_not_raw_tokens(monkeypatch, as_terminal):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: not as_terminal)
    stored = {**PAGE, "content": "# Onboarding\n\nPair for a week {{artifact:v1-u1}}."}
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=stored)
    respx.get(f"{PAGES}/{PAGE_ID}/sources").respond(json=_envelope([]))
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(
        json={
            "content": [{"type": "text", "text": "Pair for a week (handbook.pdf \u203a p. 2)."}],
            "outcome": {"status": "success"},
        }
    )

    result = runner.invoke(app, ["kb", "cat", PAGE_ID])

    assert result.exit_code == 0, result.output
    assert "{{artifact:" not in result.stdout
    assert "Pair for a week (handbook.pdf \u203a p. 2)." in result.stdout
    assert json.loads(door.calls.last.request.content) == {"command": "cat", "page": PAGE_ID}
    as_json = runner.invoke(app, ["kb", "cat", PAGE_ID, "--json"])
    assert json.loads(as_json.stdout) == {**stored, "sources": []}


# Root cause: a cut `cat` names how to continue (section_id=, source_offset=)
# but the CLI had no options for them, so the hint could not be followed.
@respx.mock
def test_cat_takes_the_continuation_arguments_a_cut_read_names():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(json=_door("Records 4-6."))

    result = runner.invoke(
        app,
        ["kb", "cat", PAGE_ID, "--section-id", "sec_a", "--full", "--source-offset", "3"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(door.calls.last.request.content) == {
        "command": "cat",
        "page": PAGE_ID,
        "section_id": "sec_a",
        "detail": "full",
        "source_offset": 3,
    }


# Root cause: grep's continuation ("continue with after_title=... after_page_id=...")
# and a grep hit's line address (cat lines=) named arguments the CLI had no
# options for. Every continuation the door names now has a flag.
@respx.mock
def test_grep_and_cat_take_every_continuation_the_door_names():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(json=_door("ok"))

    grep = runner.invoke(
        app,
        ["kb", "grep", "NW-217", "--after-title", "Helios", "--after-page-id", PAGE_ID],
    )
    cat = runner.invoke(app, ["kb", "cat", PAGE_ID, "--lines", "4-9"])

    assert grep.exit_code == 0, grep.output
    assert cat.exit_code == 0, cat.output
    sent = [json.loads(call.request.content) for call in door.calls]
    assert sent[0] == {
        "command": "grep",
        "query": "NW-217",
        "after_title": "Helios",
        "after_page_id": PAGE_ID,
    }
    assert sent[1] == {"command": "cat", "page": PAGE_ID, "lines": "4-9"}


def test_cat_source_offset_needs_full():
    result = runner.invoke(app, ["kb", "cat", PAGE_ID, "--source-offset", "3"])
    assert result.exit_code == 2
    assert "--full" in result.output


# Root cause: `kb cat --rev` read `content` from the revisions list, whose
# WikiRevision rows carry no text (revision, created_at, summary, sources), so it
# printed nothing, and it looked only at the first page of that paginated list.
# The text is read from GET .../as-of at the moment the revision was written.
_REVISIONS = f"{PAGES}/{PAGE_ID}/revisions"
_AS_OF = {
    "page_id": PAGE_ID,
    "at": "2026-09-01T10:00:00.123456+00:00",
    "revision": 2,
    "revision_created_at": "2026-09-01T10:00:00.123456+00:00",
    "compiled_by": "system",
    "summary": "Adds the pairing week.",
    "content": "# Onboarding\n\nThe older text.",
}


_COMMAND = f"{API}/v1/knowledge/wiki/command"


def _door(text: str) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "outcome": {"status": "success", "error_code": None},
        "sources": [],
    }


@respx.mock
def test_cat_rev_reads_the_revision_through_the_knowledge_door(terminal):
    # Root cause: --rev read GET .../as-of, a route outside the documented
    # contract; the door's `cat` takes `rev` (2026-10-12).
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    door = respx.post(_COMMAND).respond(json=_door("The older text."))
    as_of = respx.get(f"{PAGES}/{PAGE_ID}/as-of")

    result = runner.invoke(app, ["kb", "cat", PAGE_ID, "--rev", "2"])
    assert result.exit_code == 0, result.output
    sent = json.loads(door.calls.last.request.content)
    assert sent["command"] == "cat"
    assert sent["page"] == PAGE_ID
    assert sent["rev"] == 2
    assert "The older text." in result.stdout
    assert not as_of.called

    as_json = runner.invoke(app, ["kb", "cat", PAGE_ID, "--rev", "2", "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.stdout)["content"][0]["text"] == "The older text."


@respx.mock
def test_cat_unknown_revision_fails():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    respx.post(_COMMAND).respond(
        json={
            "content": [{"type": "text", "text": "Revision 9 not found."}],
            "outcome": {"status": "error", "error_code": "revision_not_found"},
        }
    )
    result = runner.invoke(app, ["kb", "cat", PAGE_ID, "--rev", "9"])
    assert result.exit_code != 0
    assert "Revision 9 not found" in result.output


@respx.mock
def test_log_without_a_page_reads_the_door_log():
    # Root cause: the global log read ingestion events, a route outside the
    # documented contract; the door's `log` lists recent revisions in scope.
    door = respx.post(_COMMAND).respond(json=_door("rev 4  Launch Plan"))
    result = runner.invoke(app, ["kb", "log", "--project", "p1", "--json"])
    assert result.exit_code == 0, result.output
    sent = json.loads(door.calls.last.request.content)
    assert sent["command"] == "log"
    assert "page" not in sent


@respx.mock
def test_cat_missing_page_fails():
    respx.get(f"{PAGES}/by-title").respond(404, json={"error": {"code": "not_found"}})
    respx.get(url__regex=rf"{PAGES}/search.*").respond(json=_envelope([]))
    respx.get(url__regex=rf"{API}/v1/knowledge/search.*").respond(json=_envelope([]))
    result = runner.invoke(app, ["kb", "cat", "No Such Page"])
    # Not found is exit 4 everywhere; a missing page used to exit 1.
    assert result.exit_code == 4
    assert "Page not found: No Such Page" in result.stderr


# ---------------------------------------------------------------------------
# kb provenance
# ---------------------------------------------------------------------------

PROVENANCE = {
    "provenance": {
        "s-intro": {
            "title": "Intro",
            "sources": [
                {"stance": "support", "source_title": "Handbook.pdf", "inline": True, "key": "k1"},
                {"stance": "contradicts", "source_title": "Old wiki"},
            ],
        }
    }
}


@respx.mock
def test_provenance_json_prints_the_rollup():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    route = respx.get(f"{PAGES}/{PAGE_ID}/section-provenance").respond(json=PROVENANCE)
    result = runner.invoke(app, ["kb", "provenance", PAGE_ID, "--json"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert json.loads(result.stdout) == PROVENANCE["provenance"]


@respx.mock
def test_provenance_renders_each_section_with_stance(terminal):
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    respx.get(f"{PAGES}/{PAGE_ID}/section-provenance").respond(json=PROVENANCE)
    result = runner.invoke(app, ["kb", "provenance", PAGE_ID])
    assert result.exit_code == 0, result.output
    assert "Intro" in result.stdout
    assert "support inline" in result.stdout
    assert "Handbook.pdf (k1)" in result.stdout
    assert "contradicts" in result.stdout and "Old wiki" in result.stdout


@respx.mock
def test_provenance_without_data_says_so(terminal):
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    respx.get(f"{PAGES}/{PAGE_ID}/section-provenance").respond(json={"provenance": {}})
    result = runner.invoke(app, ["kb", "provenance", PAGE_ID])
    assert result.exit_code == 0, result.output
    assert "No section provenance data." in result.stderr


# ---------------------------------------------------------------------------
# kb proposals
# ---------------------------------------------------------------------------

PROPOSAL = {
    "id": "prop-1234567890",
    "proposal_type": "edit",
    "status": "pending",
    "page_title": "Onboarding",
    "summary": "Add the buddy system",
    "payload": {"content": "New hires pair for two weeks."},
}


@respx.mock
def test_proposals_list_sends_status_limit_and_project():
    route = respx.get(PROPOSALS).respond(json=_envelope([PROPOSAL]))
    result = runner.invoke(
        app,
        [
            "kb",
            "proposals",
            "ls",
            "--status",
            "accepted",
            "--limit",
            "5",
            "--project",
            PROJECT,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    params = route.calls[0].request.url.params
    assert params["status"] == "accepted"
    assert params["limit"] == "5"
    assert params["project_id"] == PROJECT
    assert json.loads(result.stdout) == [PROPOSAL]


@respx.mock
def test_proposals_list_alias_still_lists_with_clean_json_stdout():
    # `list` was renamed to the spec verb `ls`; the old name stays as a hidden,
    # deprecated alias so existing scripts keep working, and its deprecation
    # notice goes to stderr, never into the JSON on stdout.
    respx.get(PROPOSALS).respond(json=_envelope([PROPOSAL]))
    result = runner.invoke(app, ["kb", "proposals", "list", "--project", PROJECT, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [PROPOSAL]
    assert "deprecated" in result.stderr


@respx.mock
def test_proposals_list_renders_a_table(terminal):
    respx.get(PROPOSALS).respond(json=_envelope([PROPOSAL]))
    result = runner.invoke(app, ["kb", "proposals", "ls", "--project", PROJECT])
    assert result.exit_code == 0, result.output
    assert "prop-123" in result.stdout
    assert "Onboarding" in result.stdout
    assert "Add the buddy system" in result.stdout


@respx.mock
def test_proposals_show_json_and_human(terminal):
    respx.get(f"{PROPOSALS}/prop-1234567890").respond(json=PROPOSAL)
    as_json = runner.invoke(app, ["kb", "proposals", "show", "prop-1234567890", "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.stdout) == PROPOSAL

    human = runner.invoke(app, ["kb", "proposals", "show", "prop-1234567890"])
    assert human.exit_code == 0, human.output
    assert "Proposal: prop-1234567890" in human.stdout
    assert "Summary: Add the buddy system" in human.stdout
    assert "New hires pair for two weeks." in human.stdout


HASH_A = "a" * 64
HASH_B = "b" * 64


def _read_for_accept(*, changes_card_tables: bool = False) -> respx.Route:
    """The read an accept without --reviewed-hash makes, only to classify the proposal."""
    return respx.get(f"{PROPOSALS}/prop-1").respond(
        json={
            "id": "prop-1",
            "status": "pending",
            "content_hash": HASH_B,
            "changes_card_tables": changes_card_tables,
        }
    )


def _body(route: respx.Route) -> dict | None:
    content = route.calls.last.request.content
    return json.loads(content) if content else None


@respx.mock
@pytest.mark.parametrize(("verb", "status"), [("accept", "accepted"), ("dismiss", "dismissed")])
def test_proposals_accept_and_dismiss_with_yes(verb, status):
    _read_for_accept()
    route = respx.post(f"{PROPOSALS}/prop-1/{verb}").respond(
        json={"proposal_id": "prop-1", "status": status}
    )
    result = runner.invoke(app, ["kb", "proposals", verb, "prop-1", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert json.loads(result.stdout) == {"proposal_id": "prop-1", "status": status}


@respx.mock
@pytest.mark.parametrize(("verb", "word"), [("accept", "Accepted"), ("dismiss", "Dismissed")])
def test_proposals_accept_and_dismiss_human_output(terminal, verb, word):
    _read_for_accept()
    respx.post(f"{PROPOSALS}/prop-1/{verb}").respond(json={"proposal_id": "prop-1"})
    result = runner.invoke(app, ["kb", "proposals", verb, "prop-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert f"{word} proposal prop-1." in result.stdout


@respx.mock
def test_proposals_accept_conflict_exits_5():
    _read_for_accept()
    respx.post(f"{PROPOSALS}/prop-1/accept").respond(
        409, json={"error": {"code": "conflict", "message": "The page changed since."}}
    )
    result = runner.invoke(app, ["kb", "proposals", "accept", "prop-1", "--yes"])
    assert result.exit_code == 5, result.output


# ROOT CAUSE (security review, 2026-10-16): accept read the proposal and echoed
# that fresh read's content_hash, which bound the accept to whatever the proposal
# was at accept time rather than to the read someone reviewed, so a proposal that
# changed in between was accepted anyway. The hash now comes only from a review:
# --reviewed-hash, or the read this command displayed and the person confirmed.


@respx.mock
def test_accept_with_the_shown_hash_is_refused_when_the_proposal_changed_since():
    _read_for_accept(changes_card_tables=True)  # the stored proposal now hashes to B
    accept = respx.post(f"{PROPOSALS}/prop-1/accept").respond(
        409,
        json={
            "error": {
                "code": "proposal_changed",
                "message": "This proposal changed after it was reviewed, so nothing was applied.",
            }
        },
    )

    result = runner.invoke(
        app, ["kb", "proposals", "accept", "prop-1", "--reviewed-hash", HASH_A, "--yes"]
    )

    assert result.exit_code == 5, result.output
    assert "beakr kb proposals show" in result.output
    assert _body(accept) == {"reviewed_hash": HASH_A}


@respx.mock
def test_accept_with_a_reviewed_hash_sends_it_with_no_read_of_its_own():
    read = _read_for_accept(changes_card_tables=True)
    accept = respx.post(f"{PROPOSALS}/prop-1/accept").respond(json={"proposal_id": "prop-1"})

    result = runner.invoke(
        app, ["kb", "proposals", "accept", "prop-1", "--reviewed-hash", HASH_B, "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert not read.called
    assert _body(accept) == {"reviewed_hash": HASH_B}


@respx.mock
def test_a_card_table_accept_without_a_reviewed_hash_is_refused_non_interactively():
    _read_for_accept(changes_card_tables=True)
    accept = respx.post(f"{PROPOSALS}/prop-1/accept").respond(json={"proposal_id": "prop-1"})

    result = runner.invoke(app, ["kb", "proposals", "accept", "prop-1", "--yes"])

    assert result.exit_code == 2, result.output
    assert "--reviewed-hash" in result.output
    assert not accept.called


@respx.mock
def test_a_prose_accept_without_a_hash_sends_none_not_a_fresh_reads_hash():
    _read_for_accept(changes_card_tables=False)
    accept = respx.post(f"{PROPOSALS}/prop-1/accept").respond(json={"proposal_id": "prop-1"})

    result = runner.invoke(app, ["kb", "proposals", "accept", "prop-1", "--yes"])

    assert result.exit_code == 0, result.output
    assert _body(accept) is None


@respx.mock
def test_an_interactive_accept_binds_to_the_read_it_displayed(terminal, monkeypatch):
    monkeypatch.setattr("beakr_cli.commands.kb.is_interactive", lambda: True)
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    _read_for_accept(changes_card_tables=True)
    accept = respx.post(f"{PROPOSALS}/prop-1/accept").respond(json={"proposal_id": "prop-1"})

    result = runner.invoke(app, ["kb", "proposals", "accept", "prop-1"], input="y\n")

    assert result.exit_code == 0, result.output
    assert f"Content hash: {HASH_B}" in result.stdout
    assert _body(accept) == {"reviewed_hash": HASH_B}


@respx.mock
def test_proposals_show_prints_the_content_hash_to_accept_with(terminal):
    _read_for_accept(changes_card_tables=True)

    result = runner.invoke(app, ["kb", "proposals", "show", "prop-1"])

    assert result.exit_code == 0, result.output
    assert f"Content hash: {HASH_B}" in result.stdout
    assert "--reviewed-hash" in result.stdout


@respx.mock
def test_a_reviewed_hash_that_is_not_a_content_hash_is_a_usage_error():
    accept = respx.post(f"{PROPOSALS}/prop-1/accept").respond(json={"proposal_id": "prop-1"})

    result = runner.invoke(
        app, ["kb", "proposals", "accept", "prop-1", "--reviewed-hash", "latest", "--yes"]
    )

    assert result.exit_code == 2, result.output
    assert not accept.called


# Root cause: accept ran confirm_or_require_yes whenever the interactive review
# was skipped, so --reviewed-hash without --yes still exited 2 without a
# terminal. The reviewed hash names the read that was reviewed and confirmed;
# it is the confirmation.
@respx.mock
def test_accept_with_a_reviewed_hash_needs_no_yes_without_a_terminal():
    accept = respx.post(f"{PROPOSALS}/prop-1/accept").respond(json={"proposal_id": "prop-1"})

    result = runner.invoke(app, ["kb", "proposals", "accept", "prop-1", "--reviewed-hash", HASH_B])

    assert result.exit_code == 0, result.output
    assert _body(accept) == {"reviewed_hash": HASH_B}


_EDIT_PROPOSAL = {
    "id": "prop-1",
    "proposal_type": "edit",
    "status": "pending",
    "page_title": "Onboarding",
    "summary": "Extend pairing",
    "content_hash": HASH_B,
    "payload": {
        "type": "edit",
        "page_title": "Onboarding",
        "current_content": "# Onboarding\n\nNew hires pair for a week.\nThen they ship.",
        "proposed_content": "# Onboarding\n\nNew hires pair for two weeks.\nThen they ship.",
        "rationale": "The 2026-10 retro asked for longer pairing.",
        "expected_revision": 3,
        "sections": [],
    },
}


# Root cause: show (and accept's review step) printed the payload as indented
# JSON, an edit as two whole copies of the page with \n escapes, and the content
# hash printed above it scrolled out of view. The payload is now a diff and
# labelled fields, and the hash is the last line.
@respx.mock
def test_proposals_show_renders_the_payload_readably_with_the_hash_last(terminal):
    respx.get(f"{PROPOSALS}/prop-1").respond(json=_EDIT_PROPOSAL)

    result = runner.invoke(app, ["kb", "proposals", "show", "prop-1"])

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert '"current_content"' not in out and "{" not in out
    assert "-New hires pair for a week." in out
    assert "+New hires pair for two weeks." in out
    assert "Rationale: The 2026-10 retro asked for longer pairing." in out
    assert "Expected revision: 3" in out
    assert out.rstrip().splitlines()[-1] == f"Content hash: {HASH_B}"


@respx.mock
def test_an_interactive_accept_review_shows_the_readable_payload(terminal, monkeypatch):
    monkeypatch.setattr("beakr_cli.commands.kb.is_interactive", lambda: True)
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    respx.get(f"{PROPOSALS}/prop-1").respond(json=_EDIT_PROPOSAL)
    respx.post(f"{PROPOSALS}/prop-1/accept").respond(json={"proposal_id": "prop-1"})

    result = runner.invoke(app, ["kb", "proposals", "accept", "prop-1"], input="y\n")

    assert result.exit_code == 0, result.output
    assert '"proposed_content"' not in result.stdout
    assert "+New hires pair for two weeks." in result.stdout
