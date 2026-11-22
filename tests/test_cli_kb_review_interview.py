"""``beakr kb review interview``: draft an interview from a project's open review flags.

Over the documented ``POST /v1/projects/{project_id}/interviews/from-reviews``
(beakr-engine #3109, shapes ``InterviewFromReviewsBody`` and
``InterviewFromReviewsOut``). These pin what the command sends, how the draft
reads at a terminal, piped and as JSON, and that nothing is ever sent: the
command only drafts and prints where a person edits and sends it.
"""

from __future__ import annotations

import json

import pytest
import respx
import typer
from typer.testing import CliRunner

from beakr_cli.commands import review_interview as command
from beakr_cli.main import app

API = "https://api.test"
PROJECT = "p1"
FROM_REVIEWS = f"{API}/v1/projects/{PROJECT}/interviews/from-reviews"
FLAG = "6f1c0e7a-2b3d-4c5e-8f90-a1b2c3d4e5f6"
SKIPPED = "0a9b8c7d-6e5f-4a3b-8c2d-1e0f9a8b7c6d"
EDIT_URL = "https://app.example/interviews/send?edit=s1"
runner = CliRunner()

DRAFTED = {
    "interview": {"id": "s1", "title": "Settle the Lot 42 questions", "links": []},
    "questions": [
        {
            "number": 1,
            "question": "Which release date of Lot 42 is right, 3 May or 5 May?",
            "proposal_id": FLAG,
            "kind": "contradiction",
            "page_id": "pg1",
            "page_title": "Lot 42",
            "section_id": "release",
            "reason": "Two records give different release dates.",
            "asked_revision": 4,
            "written_by": "model",
            "linked": True,
        }
    ],
    "skipped": [{"proposal_id": SKIPPED, "reason": "not_open"}],
    "questions_written_by": "model",
    "edit_url": EDIT_URL,
    "empty_reason": None,
    "message": None,
}
NOTHING = {
    "interview": None,
    "questions": [],
    "skipped": [],
    "questions_written_by": None,
    "edit_url": None,
    "empty_reason": "no_open_flags",
    "message": "This project has no open review flags.",
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def at_terminal(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _invoke(*args: str):
    return runner.invoke(app, ["kb", "review", "interview", "-P", PROJECT, *args])


@pytest.mark.parametrize(
    ("args", "body"),
    [
        ([], {}),
        (["--include", "ambiguity, likely_error"], {"include": ["ambiguity", "likely_error"]}),
        (
            ["--flags", f"{FLAG},{SKIPPED}", "--for", " Dana ", "--max-questions", "5"],
            {"proposal_ids": [FLAG, SKIPPED], "participant": "Dana", "max_questions": 5},
        ),
        (
            ["--title", "Lot 42", "--goals", "Settle the dates"],
            {"title": "Lot 42", "goals": "Settle the dates"},
        ),
        (["--flags", " , ", "--include", "", "--for", "  "], {}),
    ],
    ids=["defaults", "include", "flags-for-max", "title-goals", "blanks"],
)
@respx.mock
def test_it_sends_only_what_was_given(args, body):
    route = respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    result = _invoke(*args, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == body
    assert json.loads(result.output) == DRAFTED


@respx.mock
def test_an_unknown_kind_is_refused_before_anything_is_sent():
    result = _invoke("--include", "ambiguity,typo")
    assert result.exit_code == 2
    assert "typo" in result.output
    assert not respx.calls


@pytest.mark.parametrize("value", ["0", "31"])
@respx.mock
def test_max_questions_outside_1_to_30_is_a_usage_error(value):
    result = _invoke("--max-questions", value)
    assert result.exit_code == 2
    assert not respx.calls


@respx.mock
def test_the_draft_reads_at_a_terminal(at_terminal):
    respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    result = _invoke()
    assert result.exit_code == 0, result.output
    out = result.output
    assert 'Drafted "Settle the Lot 42 questions" (interview s1): 1 question(s)' in out
    assert "Nothing was sent." in out
    assert "1. Which release date of Lot 42 is right, 3 May or 5 May?" in out
    assert f"flag {FLAG} · contradiction · Lot 42" in out
    assert "Two records give different release dates." in out
    assert f"Skipped: {SKIPPED} (not_open)." in out
    assert f"Edit and send it in Beakr: {EDIT_URL}" in out
    assert "built from their flag" not in out


@respx.mock
def test_template_questions_are_said_so(at_terminal):
    respx.post(FROM_REVIEWS).respond(201, json={**DRAFTED, "questions_written_by": "template"})
    result = _invoke()
    assert "Some questions were built from their flag's text" in result.output


@respx.mock
def test_the_draft_piped_is_one_line_per_question_and_skipped_flag():
    respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    result = _invoke()
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0].split("\t") == [
        "interview",
        "s1",
        "Settle the Lot 42 questions",
        "questions=1",
        "written_by=model",
        f"edit_url={EDIT_URL}",
    ]
    assert lines[1].split("\t") == [
        "1",
        FLAG,
        "contradiction",
        "Lot 42",
        "Which release date of Lot 42 is right, 3 May or 5 May?",
        "reason=Two records give different release dates.",
    ]
    assert lines[2].split("\t") == ["skipped", SKIPPED, "not_open"]


@respx.mock
def test_nothing_to_ask_is_not_an_error(at_terminal):
    respx.post(FROM_REVIEWS).respond(200, json=NOTHING)
    result = _invoke()
    assert result.exit_code == 0, result.output
    assert "Nothing drafted: This project has no open review flags." in result.output
    assert "Edit and send" not in result.output


@respx.mock
def test_nothing_to_ask_piped():
    respx.post(FROM_REVIEWS).respond(200, json=NOTHING)
    result = _invoke()
    assert result.output.strip().split("\t") == [
        "nothing_drafted",
        "no_open_flags",
        "This project has no open review flags.",
    ]


@respx.mock
def test_a_key_without_the_scope_exits_3():
    respx.post(FROM_REVIEWS).respond(
        403, json={"error": {"code": "insufficient_scope", "message": "Needs interviews:write."}}
    )
    result = _invoke()
    assert result.exit_code == 3, result.output


def test_the_command_joins_a_review_group_another_module_made(monkeypatch):
    kb = typer.Typer()
    existing = typer.Typer()
    kb.add_typer(existing, name="review")
    monkeypatch.setattr(command, "kb_app", kb)
    command.register(typer.Typer())
    assert [group.name for group in kb.registered_groups] == ["review"]
    assert [c.name for c in existing.registered_commands] == ["interview"]


def test_the_command_makes_the_review_group_when_there_is_none(monkeypatch):
    kb = typer.Typer()
    monkeypatch.setattr(command, "kb_app", kb)
    command.register(typer.Typer())
    (group,) = kb.registered_groups
    assert group.name == "review"
    assert [c.name for c in group.typer_instance.registered_commands] == ["interview"]


def test_the_cli_has_one_review_group():
    from beakr_cli.commands.kb import app as kb_app

    assert [g.name for g in kb_app.registered_groups].count("review") == 1
