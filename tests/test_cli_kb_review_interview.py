"""``beakr kb review interview``: turn a project's open review flags into one interview (ENG-2467).

Over the documented ``POST /v1/projects/{project_id}/interviews/from-reviews``
(beakr-engine #3109, shapes ``InterviewFromReviewsBody`` and
``InterviewFromReviewsOut``). These pin what the command sends, how the draft
reads at a terminal, piped and as JSON (a draft, or a person's link printed once),
the flags left out past the question limit, and how a flag is then resolved.
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
LEFT_OUT = "3c2b1a09-8f7e-4d6c-9b5a-4f3e2d1c0b9a"
LINK = "5e4d3c2b-1a09-4f8e-8d7c-6b5a4f3e2d1c"
LINK_URL = "https://app.example/i/once-token"
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
    "skipped": [
        {"proposal_id": SKIPPED, "reason": "not_open"},
        {"proposal_id": LEFT_OUT, "reason": "over_question_limit"},
    ],
    "questions_written_by": "model",
    "edit_url": EDIT_URL,
    "empty_reason": None,
    "message": None,
}
WITH_LINK = {
    **DRAFTED,
    "link": {
        "link_id": LINK,
        "label": "Dana Reyes",
        "expires_at": "2026-12-06T21:00:00Z",
        "result_url": f"{API}/v1/interview/sendables/links/{LINK}/result",
    },
    "link_url": LINK_URL,
    "secret_redacted": False,
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
        (["--kinds", "contradiction, likely_error"], {"kinds": ["contradiction", "likely_error"]}),
        (
            ["--flags", f"{FLAG},{SKIPPED}", "--max-questions", "5"],
            {"proposal_ids": [FLAG, SKIPPED], "max_questions": 5},
        ),
        (
            ["--title", "Lot 42", "--goals", "Settle the dates"],
            {"title": "Lot 42", "goals": "Settle the dates"},
        ),
        (
            ["--for", " Dana Reyes ", "--email", "dana@example.com", "--expires-in-days", "7"],
            {
                "participant": {"name": "Dana Reyes", "email": "dana@example.com"},
                "link_expires_in_days": 7,
            },
        ),
        (["--user-id", "u1"], {"participant": {"user_id": "u1"}}),
        (["--flags", " , ", "--kinds", "", "--for", "  "], {}),
    ],
    ids=["defaults", "kinds", "flags-max", "title-goals", "name-email", "member", "blanks"],
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
    result = _invoke("--kinds", "ambiguity,all")
    assert result.exit_code == 2
    assert "all" in result.output
    assert not respx.calls


@respx.mock
def test_a_link_expiry_without_a_person_is_refused_before_anything_is_sent():
    result = _invoke("--expires-in-days", "7")
    assert result.exit_code == 2
    assert not respx.calls


@pytest.mark.parametrize(
    "args",
    [
        ["--max-questions", "0"],
        ["--max-questions", "31"],
        ["--for", "D", "--expires-in-days", "91"],
    ],
)
@respx.mock
def test_numbers_out_of_range_are_usage_errors(args):
    result = _invoke(*args)
    assert result.exit_code == 2
    assert not respx.calls


@respx.mock
def test_the_draft_reads_at_a_terminal(at_terminal):
    respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    result = _invoke()
    assert result.exit_code == 0, result.output
    out = result.output
    assert 'Drafted "Settle the Lot 42 questions" (interview s1): 1 question(s)' in out
    assert "1. Which release date of Lot 42 is right, 3 May or 5 May?" in out
    assert f"flag {FLAG} · contradiction · Lot 42" in out
    assert "Two records give different release dates." in out
    assert "Left out (1 open flag(s) past the question limit" in out
    assert LEFT_OUT in out
    assert f"Skipped: {SKIPPED} (not_open)." in out
    assert f"Nothing was sent. Edit and send it in Beakr: {EDIT_URL}" in out
    assert "built from their flag" not in out
    # How the flag is resolved from its answer, with the kb review commands.
    # A CLI-drafted interview is never applied by itself (the engine records
    # creator_cannot_resolve for a key or OAuth sign-in), so the steps say so.
    assert "An interview drafted here is never applied by itself" in out
    assert f"beakr kb review options {FLAG}" in out
    assert f"beakr kb review resolve {FLAG} --option <the option an answer points to>" in out


@respx.mock
def test_a_persons_link_is_printed_once_with_who_it_is_for_and_the_result(at_terminal):
    respx.post(FROM_REVIEWS).respond(201, json=WITH_LINK)
    result = _invoke("--for", "Dana Reyes")
    assert result.exit_code == 0, result.output
    out = result.output
    assert 'Made "Settle the Lot 42 questions"' in out
    assert "Link for Dana Reyes, shown only once (Beakr sends nothing; send it):" in out
    assert f"  {LINK_URL}" in out
    assert f"Link id {LINK}, expires 2026-12-06T21:00:00Z." in out
    assert f"Result: {API}/v1/interview/sendables/links/{LINK}/result" in out
    assert f"beakr interviews link show {LINK}" in out
    assert "Edit and send it in Beakr" not in out


@respx.mock
def test_a_replay_says_the_link_is_not_shown_again(at_terminal):
    replay = {**WITH_LINK, "link_url": None, "secret_redacted": True}
    respx.post(FROM_REVIEWS).respond(201, json=replay)
    result = _invoke("--for", "Dana Reyes")
    assert "not shown again" in result.output
    assert LINK_URL not in result.output


@respx.mock
def test_a_persons_link_piped_is_on_the_interview_line():
    respx.post(FROM_REVIEWS).respond(201, json=WITH_LINK)
    result = _invoke("--user-id", "u1")
    first, *_, last = result.output.strip().splitlines()
    fields = first.split("\t")
    assert f"link_id={LINK}" in fields
    assert "for=Dana Reyes" in fields
    assert f"link_url={LINK_URL}" in fields
    assert last == f"next: beakr interviews link show {LINK}"


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
    assert lines[3].split("\t") == ["skipped", LEFT_OUT, "over_question_limit"]
    assert len(lines) == 4


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
def test_an_unknown_person_exits_4():
    respx.post(FROM_REVIEWS).respond(
        404, json={"error": {"code": "participant_not_found", "message": "No such member."}}
    )
    result = _invoke("--user-id", "u1")
    assert result.exit_code == 4, result.output


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
