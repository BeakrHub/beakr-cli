"""Read flow: a partial read says so, a cut list names its continuation, ``cat --json``
is the bounded read, and the commands a read's hints name exist.

Reproduced on 2026-11-13 against beakr-cli 0.5.0; each test names its root cause.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import compact
from beakr_cli.main import app

API = "https://api.test"
PAGE_ID = "33333333-3333-3333-3333-333333333333"
PROJECT = "22222222-2222-2222-2222-222222222222"
PAGES = f"{API}/v1/knowledge/wiki/pages"
DOOR = f"{API}/v1/knowledge/wiki/command"
DOCUMENTS = f"{API}/v1/knowledge/documents"
runner = CliRunner()

PAGE = {
    "id": PAGE_ID,
    "title": "Onboarding",
    "page_type": "process",
    "revision": 3,
    "project_id": PROJECT,
    "content": "# Onboarding\n\nNew hires pair for a week.",
}

#: A door reply whose text and hints say nothing about being cut: the coverage
#: object is the only sign that the read is partial.
PARTIAL_COVERAGE = {
    "command": "grep",
    "complete": False,
    "truncated": True,
    "continue_with": {"after_title": "Onboarding", "after_page_id": PAGE_ID},
}
PARTIAL = {
    "content": [{"type": "text", "text": "Onboarding:3: New hires pair for a week."}],
    "outcome": {"status": "success"},
    "coverage": PARTIAL_COVERAGE,
    "sources": [],
    "next": [],
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.delenv("BEAKR_OUTPUT", raising=False)


def _as(monkeypatch, mode: str) -> list[str]:
    """Run as a person at a terminal, through a pipe, or with ``--json``; the extra argv."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: mode != "terminal")
    return ["--json"] if mode == "json" else []


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


def _invoked_as(monkeypatch, *argv: str):
    """Invoke ``beakr <argv>`` with the process arguments a real run would have."""
    monkeypatch.setattr(compact.sys, "argv", ["beakr", *argv])
    return runner.invoke(app, list(argv))


# ---------------------------------------------------------------------------
# 1. A partial read says it is partial
# ---------------------------------------------------------------------------


# Root cause: `_door_read` rebuilt the reply as {content, sources, next} before
# rendering, dropping `coverage`, so the shared renderer (which prints partial
# coverage) never saw it and a cut read looked whole. --json already printed the
# reply unchanged; it is pinned here so the three outputs stay in step.
@respx.mock
@pytest.mark.parametrize("mode", ["terminal", "piped", "json"])
def test_a_partial_read_prints_its_coverage_in_every_output(monkeypatch, mode):
    respx.post(DOOR).respond(json=PARTIAL)

    result = runner.invoke(app, ["kb", "grep", "pair", *_as(monkeypatch, mode)])

    assert result.exit_code == 0, result.output
    if mode == "json":
        assert json.loads(result.stdout)["coverage"] == PARTIAL_COVERAGE
        return
    assert "Onboarding:3: New hires pair for a week." in result.stdout
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("Coverage: "))
    assert json.loads(line.removeprefix("Coverage: ")) == PARTIAL_COVERAGE


@respx.mock
@pytest.mark.parametrize("mode", ["terminal", "piped"])
def test_a_complete_read_prints_no_coverage_line(monkeypatch, mode):
    whole = {**PARTIAL, "coverage": {"command": "grep", "complete": True, "returned": 1}}
    respx.post(DOOR).respond(json=whole)

    result = runner.invoke(app, ["kb", "grep", "pair", *_as(monkeypatch, mode)])

    assert result.exit_code == 0, result.output
    assert "Coverage:" not in result.stdout


# ---------------------------------------------------------------------------
# 2. `kb ls --limit` says when the list goes on, and continues it
# ---------------------------------------------------------------------------


def _rows(*titles: str) -> list[dict]:
    return [{"id": f"p-{t.lower()}", "title": t, "page_type": "topic"} for t in titles]


def _two_page_list() -> respx.Route:
    def answer(request):
        if request.url.params.get("cursor") == "c2":
            body = {"data": _rows("Gamma"), "next_cursor": None, "has_more": False}
        else:
            body = {"data": _rows("Alpha", "Beta"), "next_cursor": "c2", "has_more": True}
        return respx.MockResponse(200, json=body)

    return respx.get(PAGES).mock(side_effect=answer)


# Root cause: `kb ls` read `pages.iter` through `islice(limit)` and printed the
# rows, discarding has_more and the cursor, so `--limit 2` of a longer list
# looked like the whole list and there was no option to continue it.
@respx.mock
@pytest.mark.parametrize("mode", ["terminal", "piped"])
def test_ls_cut_by_limit_ends_with_the_command_that_continues_it(monkeypatch, mode):
    route = _two_page_list()
    _as(monkeypatch, mode)

    result = _invoked_as(monkeypatch, "kb", "ls", "-P", PROJECT, "--limit", "2")

    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["limit"] == "2"
    assert "Alpha" in result.stdout and "Beta" in result.stdout
    assert result.stdout.rstrip().splitlines()[-1] == (
        "Incomplete: 2 pages listed and more exist. "
        f"Continue with: beakr kb ls -P {PROJECT} --limit 2 --cursor c2"
    )


@respx.mock
def test_ls_json_stays_a_plain_array_and_says_it_is_incomplete_on_stderr(monkeypatch):
    _two_page_list()

    result = _invoked_as(monkeypatch, "kb", "ls", "-P", PROJECT, "--limit", "2", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _rows("Alpha", "Beta")
    assert "Incomplete: 2 pages listed and more exist." in " ".join(result.stderr.split())
    assert "--cursor c2" in result.stderr


@respx.mock
def test_ls_json_envelope_carries_has_more_and_the_cursor(monkeypatch):
    _two_page_list()

    result = _invoked_as(
        monkeypatch, "kb", "ls", "-P", PROJECT, "--limit", "2", "--json", "--envelope"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "data": _rows("Alpha", "Beta"),
        "has_more": True,
        "next_cursor": "c2",
    }


@respx.mock
def test_ls_cursor_continues_the_list_and_the_last_page_has_no_notice(monkeypatch):
    route = _two_page_list()
    _as(monkeypatch, "piped")

    result = _invoked_as(monkeypatch, "kb", "ls", "-P", PROJECT, "--limit", "2", "--cursor", "c2")

    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["cursor"] == "c2"
    assert "Gamma" in result.stdout
    assert "Incomplete" not in result.stdout
    enveloped = _invoked_as(
        monkeypatch, "kb", "ls", "-P", PROJECT, "--cursor", "c2", "--json", "--envelope"
    )
    assert json.loads(enveloped.stdout) == {
        "data": _rows("Gamma"),
        "has_more": False,
        "next_cursor": None,
    }


@respx.mock
def test_ls_without_a_limit_still_lists_every_page(monkeypatch):
    route = _two_page_list()

    result = _invoked_as(monkeypatch, "kb", "ls", "-P", PROJECT, "--json")

    assert result.exit_code == 0, result.output
    assert [row["title"] for row in json.loads(result.stdout)] == ["Alpha", "Beta", "Gamma"]
    assert route.call_count == 2
    assert "Incomplete" not in result.stderr


def test_ls_envelope_needs_json(monkeypatch):
    _as(monkeypatch, "piped")
    result = runner.invoke(app, ["kb", "ls", "-P", PROJECT, "--envelope"])
    assert result.exit_code == 2
    assert "--envelope" in result.stderr


# ---------------------------------------------------------------------------
# 3. `kb cat --json` is the bounded read; `--raw` is the stored-page export
# ---------------------------------------------------------------------------


def _door_reply(text: str) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "outcome": {"status": "success"},
        "coverage": {"command": "cat", "complete": False, "truncated": True},
        "next": ["Read one section with section_id=sec_a."],
        "sources": [],
    }


# Root cause: `cat PAGE --json` without a selector skipped the door and printed
# the stored page record plus every source (469,739 characters for one page),
# while `cat PAGE --section-id S --json` printed the door's bounded reply: two
# data sources and two schemas behind one flag.
@respx.mock
def test_cat_json_is_the_same_bounded_envelope_with_or_without_a_section():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    stored_sources = respx.get(f"{PAGES}/{PAGE_ID}/sources").respond(
        json={"data": [], "next_cursor": None, "has_more": False}
    )
    door = respx.post(DOOR).respond(json=_door_reply("# Onboarding\n\nNew hires pair."))

    whole = runner.invoke(app, ["kb", "cat", PAGE_ID, "--json"])
    one = runner.invoke(app, ["kb", "cat", PAGE_ID, "--section-id", "sec_a", "--json"])

    assert whole.exit_code == 0, whole.output
    assert json.loads(whole.stdout) == _door_reply("# Onboarding\n\nNew hires pair.")
    assert json.loads(whole.stdout).keys() == json.loads(one.stdout).keys()
    assert door.call_count == 2
    assert not stored_sources.called


@respx.mock
def test_cat_raw_exports_the_stored_page_and_its_sources_without_the_door():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    respx.get(f"{PAGES}/{PAGE_ID}/sources").respond(
        json={"data": [], "next_cursor": None, "has_more": False}
    )
    door = respx.post(DOOR).respond(json=_door_reply("unused"))

    result = runner.invoke(app, ["kb", "cat", PAGE_ID, "--raw"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {**PAGE, "sources": []}
    assert not door.called


def test_cat_raw_refuses_a_selector_of_the_bounded_read():
    result = runner.invoke(app, ["kb", "cat", PAGE_ID, "--raw", "--section-id", "sec_a"])
    assert result.exit_code == 2
    assert "--raw" in result.stderr


# ---------------------------------------------------------------------------
# 4. The commands a read's hints name exist
# ---------------------------------------------------------------------------


# Root cause: read hints and the tool descriptions name `cat --outline`, `hover`,
# `links direction=out`, `timeline {page}`, `provenance` and `documents read`,
# but the published CLI had none of them: each exited 2 ("No such command" /
# "No such option") and only the generic `beakr api ...` form worked.
@respx.mock
def test_cat_outline_asks_the_door_for_the_outline():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    door = respx.post(DOOR).respond(json=_door_reply("sec_a  Charter  (412 chars)"))

    result = runner.invoke(app, ["kb", "cat", PAGE_ID, "--outline"])

    assert result.exit_code == 0, result.output
    assert _body(door) == {"command": "cat", "page": PAGE_ID, "outline": True}
    assert "sec_a  Charter" in result.stdout


@respx.mock
@pytest.mark.parametrize(
    ("argv", "sent"),
    [
        (["kb", "hover", "Onboarding"], {"command": "hover", "page": "Onboarding"}),
        (["kb", "links", "Onboarding"], {"command": "links", "page": "Onboarding"}),
        (
            ["kb", "links", "Onboarding", "--direction", "out", "--limit", "5", "--offset", "5"],
            {
                "command": "links",
                "page": "Onboarding",
                "direction": "out",
                "limit": 5,
                "offset": 5,
            },
        ),
        (["kb", "timeline", "Onboarding"], {"command": "timeline", "page": "Onboarding"}),
        (
            ["kb", "timeline", "--query", "pairing", "--start", "2026-01-01", "--cursor", "c2"],
            {
                "command": "timeline",
                "timeline_query": "pairing",
                "start_date": "2026-01-01",
                "cursor": "c2",
            },
        ),
        (
            ["kb", "provenance", "Onboarding", "--after-section", "sec_a"],
            {"command": "provenance", "page": "Onboarding", "after_section": "sec_a"},
        ),
    ],
    ids=["hover", "links", "links-out", "timeline", "timeline-filters", "provenance"],
)
def test_a_graph_read_runs_the_door_command_of_the_same_name(argv, sent):
    door = respx.post(DOOR).respond(json=PARTIAL)

    result = runner.invoke(app, [*argv, "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert _body(door) == {**sent, "scope": PROJECT}
    # The same rendering as every other door read, partial coverage included.
    assert "Onboarding:3: New hires pair for a week." in result.stdout
    assert "Coverage: " in result.stdout


def test_links_refuses_an_unknown_direction():
    result = runner.invoke(app, ["kb", "links", "Onboarding", "--direction", "sideways"])
    assert result.exit_code == 2


@respx.mock
@pytest.mark.parametrize(
    ("argv", "command", "arguments"),
    [
        (
            ["documents", "read", "f-1", "--start-line", "1", "--end-line", "4"],
            "read",
            {"document": "f-1", "start_line": 1, "end_line": 4},
        ),
        (["documents", "read", "Helios/charter.pdf"], "read", {"document": "Helios/charter.pdf"}),
        (
            ["documents", "grep", "NW-217", "-F", "-i", "-C", "2", "--type", "pdf", "-n", "20"],
            "grep",
            {
                "pattern": "NW-217",
                "fixed_strings": True,
                "ignore_case": True,
                "context": 2,
                "type": ["pdf"],
                "head_limit": 20,
            },
        ),
        (
            ["documents", "ls", "Helios", "--recursive", "--limit", "10", "--after-document", "d1"],
            "ls",
            {"path": "Helios", "recursive": True, "limit": 10, "after_document": "d1"},
        ),
        (["documents", "ls"], "ls", {}),
    ],
    ids=["read-window", "read", "grep", "ls-paged", "ls"],
)
def test_a_documents_command_runs_the_documents_door(argv, command, arguments):
    door = respx.post(DOCUMENTS).respond(
        json={"content": "1  Project Helios charter", "sources": {}, "is_error": False}
    )

    result = runner.invoke(app, [*argv, "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert _body(door) == {"command": command, "arguments": arguments, "project_id": PROJECT}
    assert "1  Project Helios charter" in result.stdout


# Root cause (the same as the Knowledge reads'): the documents renderer printed
# content and hints only, so a scan the door reported as partial read as whole.
@respx.mock
@pytest.mark.parametrize("mode", ["terminal", "piped", "json"])
def test_a_partial_documents_scan_prints_its_coverage(monkeypatch, mode):
    coverage = {"complete": False, "scanned_documents": 40, "documents_in_scope": 900}
    respx.post(DOCUMENTS).respond(
        json={"content": "3 matches", "sources": {}, "is_error": False, "coverage": coverage}
    )

    result = runner.invoke(app, ["documents", "grep", "NW-217", *_as(monkeypatch, mode)])

    assert result.exit_code == 0, result.output
    if mode == "json":
        assert json.loads(result.stdout)["coverage"] == coverage
        return
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("Coverage: "))
    assert json.loads(line.removeprefix("Coverage: ")) == coverage


@respx.mock
def test_a_documents_read_the_door_refuses_exits_1_with_its_message():
    respx.post(DOCUMENTS).respond(
        json={"content": "No document matches 'nope'.", "sources": {}, "is_error": True}
    )

    result = runner.invoke(app, ["documents", "read", "nope"])

    assert result.exit_code == 1
    assert "No document matches 'nope'." in result.stdout
