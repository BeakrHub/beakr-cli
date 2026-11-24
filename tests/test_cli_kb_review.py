"""``beakr kb review`` and the stdio ``review_options``/``resolve_review`` tools.

A review flag is resolved pinned to the page revision the person chose against:

- ``resolve --option N`` applies only an option ``options`` already showed for
  the page as it is: a page that moved on, or drafts made by this very call
  (``cached: false``, never shown), are refused with nothing applied (exit 5);
- a row option goes to ``resolve-rows``, any other to ``resolve``;
- ``--edit`` opens the flagged section in $EDITOR and sends it as a rewrite;
  an unchanged buffer applies nothing;
- a 409 from the server says the page changed and to run ``options`` again;
- an agent refused because a person must accept exits 6, as an accept does.
- ``options`` reads the flag first: interview answers on an open flag are
  listed after its revision, and a flag already decided (by a person, or by
  Beakr from a person's own answer) says how instead of drafting options;
  ``resolve`` says when the row or section it left now cites an answer. An
  engine that sends none of these fields prints what it printed before.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
FLAG = "5f0c2d1e-8a7b-4c3d-9e2f-1a0b9c8d7e6f"
BASE = f"{API}/v1/knowledge/wiki/proposals/{FLAG}"
SECTION = "<!-- sec:dosing -->\n## Dosing\nIC50 is 12nM (Lee 2023).\n"
CONFLICT = {
    "proposal_id": FLAG,
    "page_id": "9a2c4e61-7b3f-4d8a-a1c5-3e6f8b0d2c41",
    "page_title": "Compound X",
    "section_id": "dosing",
    "anchor": "section",
    "section_kind": "text",
    "section_title": "Dosing",
    "section_content": SECTION,
    "reason": "Sources disagree on the IC50.",
    "current_revision": 3,
    "is_stale": False,
}
TEXT_OPTION = {
    "id": "A",
    "label": "Keep 12nM",
    "summary": "Keep Lee 2023.",
    "new_section_body": "<!-- sec:dosing -->\n## Dosing\nIC50 is 12nM; Smith saw 40nM.\n",
}
ROW_OPTION = {
    "id": "B",
    "label": "Retire the 40nM row",
    "summary": "Delete the disputing row.",
    "new_section_body": "<!-- sec:facts -->\n| ... |",
    "row_operation": {
        "table": "facts",
        "kind": "delete",
        "row_ids": ["r-2"],
        "row": None,
        "reason": "Different assay.",
    },
}
RESOLVED = {
    "status": "resolved",
    "proposal_id": FLAG,
    "page_id": CONFLICT["page_id"],
    "section_id": "dosing",
    "new_revision": 4,
}
#: The proposal read of an open flag, as an engine without interview answers sends it.
PENDING_FLAG = {"id": FLAG, "proposal_type": "review", "status": "pending"}
TRANSCRIPT = "7d1e3a90-2b4c-4f6e-9a8d-1c3b5e7f9a20"
DECISIVE_ANSWER = {
    "answered": True,
    "answered_by": "Dana Reyes",
    "answered_at": "2026-11-20T15:04:00Z",
    "answer": "12nM is right.",
    "excerpt": "12nM is right; the 40nM run used the old assay.",
    "decisive": True,
    "points_to_option": "A",
    "suggested_option_id": "A",
    "stale": False,
    "auto_resolution": {"status": "left_for_person", "reason": "participant_not_verified"},
    "source": {"reference": "Interview: IC50 questions", "artifact_id": TRANSCRIPT},
}
runner = CliRunner()


def _drafted(*, revision: int = 3, cached: bool = True) -> dict:
    return {
        "proposal_id": FLAG,
        "section_id": "dosing",
        "current_revision": revision,
        "options": [TEXT_OPTION, ROW_OPTION],
        "cached": cached,
        "no_draft_reason": None,
    }


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.delenv("BEAKR_PROJECT_ID", raising=False)


def _invoke(*args: str, mode: str = "human", **kwargs):
    return runner.invoke(app, list(args), env={"BEAKR_OUTPUT": mode, "COLUMNS": "200"}, **kwargs)


def _sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


@respx.mock
def test_options_lists_each_option_with_what_it_changes() -> None:
    respx.get(BASE).respond(200, json=PENDING_FLAG)
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    result = _invoke("kb", "review", "options", FLAG)
    assert result.exit_code == 0, result.output
    assert "1. Option A: Keep 12nM" in result.stdout
    assert "Row change: delete on facts (rows r-2)" in result.stdout
    assert f"beakr kb review resolve {FLAG} --option <number>" in result.stdout
    assert "resolve_review(" not in result.stdout
    # An engine that attaches no interview answers prints no answer lines.
    assert "Interview answers" not in result.stdout
    assert "cite that answer" not in result.stdout


@respx.mock
def test_options_lists_a_decisive_interview_answer_after_the_revision() -> None:
    flag = {**PENDING_FLAG, "interview_answers": [DECISIVE_ANSWER]}
    # The flag is read before drafting and again after: drafting may match an
    # answer to the options as they are now.
    read = respx.get(BASE).respond(200, json=flag)
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    result = _invoke("kb", "review", "options", FLAG)
    assert result.exit_code == 0, result.output
    assert read.call_count == 2
    lines = result.stdout.splitlines()
    revision = lines.index("Page revision: 3")
    assert lines[revision + 1 : revision + 4] == [
        "",
        "Interview answers:",
        '- Dana Reyes, 2026-11-20: "12nM is right; the 40nM run used the old assay." '
        "Decisive. Points to option A. "
        f"Source: Interview: IC50 questions (file {TRANSCRIPT}).",
    ]
    apply = lines.index(f"Apply one with `beakr kb review resolve {FLAG} --option <number>`.")
    assert lines[apply + 1] == (
        "Applying the option an answer points to makes the row or section it leaves cite "
        "that answer."
    )
    assert lines[-1] == f"Or write the section yourself: `beakr kb review resolve {FLAG} --edit`."


@respx.mock
def test_options_says_when_the_decisive_answers_disagree() -> None:
    other = {
        **DECISIVE_ANSWER,
        "answered_by": "Sam Ortiz",
        "answered_at": "2026-11-21T09:00:00Z",
        "excerpt": None,
        "answer": "Use 40nM, the new assay is the right one.",
        "points_to_option": "B",
        "suggested_option_id": None,
        "auto_resolution": None,
        "source": {},
    }
    first = {**DECISIVE_ANSWER, "suggested_option_id": None}
    unanswered = {"answered": False, "answered_by": "Lee Park"}
    note = "Answers disagree: A from Dana Reyes, B from Sam Ortiz."
    flag = {
        **PENDING_FLAG,
        "interview_answers": [first, unanswered, other],
        "interview_answers_note": note,
    }
    respx.get(BASE).respond(200, json=flag)
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    result = _invoke("kb", "review", "options", FLAG)
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    start = lines.index("Interview answers:")
    assert lines[start + 1].startswith('- Dana Reyes, 2026-11-20: "12nM is right;')
    assert "Points to option A." in lines[start + 1]
    # Its own words when there is no excerpt; no source part without a file.
    assert lines[start + 2] == (
        '- Sam Ortiz, 2026-11-21: "Use 40nM, the new assay is the right one." '
        "Decisive. Points to option B."
    )
    assert lines[start + 3] == note
    assert "Lee Park" not in result.stdout


@respx.mock
def test_options_marks_an_answer_about_text_that_has_changed() -> None:
    stale = {**DECISIVE_ANSWER, "stale": True, "decisive": False}
    respx.get(BASE).respond(200, json={**PENDING_FLAG, "interview_answers": [stale]})
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    result = _invoke("kb", "review", "options", FLAG)
    assert result.exit_code == 0, result.output
    assert "About text that has since changed." in result.stdout
    assert "Points to option" not in result.stdout


@respx.mock
def test_options_json_carries_the_interview_answers() -> None:
    note = "Answers agree on A."
    flag = {**PENDING_FLAG, "interview_answers": [DECISIVE_ANSWER], "interview_answers_note": note}
    respx.get(BASE).respond(200, json=flag)
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    result = _invoke("kb", "review", "options", FLAG, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["interview_answers"] == [DECISIVE_ANSWER]
    assert data["interview_answers_note"] == note
    assert data["flag"] == CONFLICT


AUTO_APPLIED = {
    "id": FLAG,
    "proposal_type": "review",
    "status": "resolved",
    "payload": {
        "resolved_by_change": {
            "approval": "interview_answer",
            "option": "A",
            "option_label": "Keep 12nM",
            "answered_by": "Dana Reyes",
            "answered_at": "2026-11-20T15:04:00Z",
            "interview_title": "IC50 questions",
        },
        "resolved_with": {"proposal_id": "0f4b6d82-3c5e-4a7f-8b19-2e6d8a0c4f51"},
    },
    "interview_answers": [
        {**DECISIVE_ANSWER, "auto_resolution": {"status": "applied", "option": "A"}}
    ],
}


@respx.mock
def test_options_says_how_a_flag_beakr_applied_from_an_answer_was_decided() -> None:
    respx.get(BASE).respond(200, json=AUTO_APPLIED)
    conflict = respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    drafts = respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    result = _invoke("kb", "review", "options", FLAG)
    assert result.exit_code == 0, result.output
    # Nothing is drafted for a flag that is no longer open.
    assert not conflict.called and not drafts.called
    assert result.stdout.splitlines() == [
        f"Review flag {FLAG} is resolved: there is nothing to resolve.",
        "Beakr applied option A (Keep 12nM) from the interview answer of Dana Reyes "
        '(2026-11-20) in "IC50 questions".',
        "Closed with review flag 0f4b6d82-3c5e-4a7f-8b19-2e6d8a0c4f51, about the same dispute.",
        "Interview answers:",
        '- Dana Reyes, 2026-11-20: "12nM is right; the 40nM run used the old assay." '
        "Decisive. Points to option A. Beakr applied option A from this answer. "
        f"Source: Interview: IC50 questions (file {TRANSCRIPT}).",
    ]


@respx.mock
def test_options_finds_a_decided_flag_by_the_start_of_its_id() -> None:
    def listed(request):
        status = request.url.params["status"]
        rows = [AUTO_APPLIED] if status == "resolved" else []
        return httpx.Response(200, json={"data": rows, "has_more": False, "next_cursor": None})

    respx.get(f"{API}/v1/knowledge/wiki/proposals").mock(side_effect=listed)
    respx.get(BASE).respond(200, json=AUTO_APPLIED)
    result = _invoke("kb", "review", "options", FLAG[:8])
    assert result.exit_code == 0, result.output
    assert f"Review flag {FLAG} is resolved" in result.stdout


@respx.mock
def test_resolve_says_the_row_it_left_cites_the_answer() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    cited = [
        {"answered_by": "Dana Reyes", "file_name": "IC50 questions.md"},
        {"answered_by": "Dana Reyes", "file_name": "IC50 questions.md"},
        {"answered_by": "Sam Ortiz", "file_name": None},
    ]
    respx.post(f"{BASE}/resolve-rows").respond(200, json={**RESOLVED, "cited_answers": cited})
    result = _invoke("kb", "review", "resolve", FLAG, "--option", "B", "--yes")
    assert result.exit_code == 0, result.output
    assert " ".join(result.stdout.split()) == (
        f"Resolved review flag {FLAG}: section dosing changed; the page is now at revision 4. "
        "It now cites the interview answer of Dana Reyes, Sam Ortiz (IC50 questions.md)."
    )


@respx.mock
@pytest.mark.parametrize(
    ("option", "path", "body"),
    [
        (
            "1",
            "resolve",
            {
                "section_id": "dosing",
                "new_section_body": TEXT_OPTION["new_section_body"],
                "expected_revision": 3,
                "option_id": "A",
            },
        ),
        ("b", "resolve-rows", {"option_id": "B", "expected_revision": 3}),
    ],
)
def test_an_option_goes_to_the_route_for_its_kind_pinned_to_the_revision(option, path, body):
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    applied = respx.post(f"{BASE}/{path}").respond(200, json=RESOLVED)
    result = _invoke("kb", "review", "resolve", FLAG, "--option", option, "--yes")
    assert result.exit_code == 0, result.output
    assert _sent(applied) == body
    assert "now at revision 4" in result.stdout


@respx.mock
def test_options_drafted_by_this_call_were_never_shown_and_are_not_applied() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted(cached=False))
    applied = respx.post(f"{BASE}/resolve").respond(200, json=RESOLVED)
    result = _invoke("kb", "review", "resolve", FLAG, "--option", "1", "--yes")
    assert result.exit_code == 5, result.output
    assert not applied.called
    assert "Nothing was applied" in result.output
    assert f"beakr kb review options {FLAG}" in result.output


@respx.mock
def test_a_revision_the_page_has_moved_past_is_refused_before_anything_is_sent() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json={**CONFLICT, "current_revision": 5})
    drafts = respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    result = _invoke("kb", "review", "resolve", FLAG, "--option", "1", "--revision", "3", "--yes")
    assert result.exit_code == 5, result.output
    assert not drafts.called
    assert "revision 3" in result.output and "5 now" in result.output


@respx.mock
def test_a_409_from_the_server_says_to_run_options_again() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    respx.post(f"{BASE}/resolve").respond(
        409,
        json={
            "detail": "The page changed",
            "error": {"code": "conflict", "message": "The page changed", "details": {}},
        },
    )
    result = _invoke("kb", "review", "resolve", FLAG, "--option", "A", "--yes")
    assert result.exit_code == 5, result.output
    assert "The page changed since you chose. Nothing was applied." in result.output
    assert f"beakr kb review options {FLAG}" in result.output


@respx.mock
def test_edit_sends_the_edited_section_pinned_to_the_revision_it_read(monkeypatch) -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    applied = respx.post(f"{BASE}/resolve").respond(200, json=RESOLVED)
    edited = SECTION.replace("12nM (Lee 2023)", "12-40nM depending on assay")
    monkeypatch.setattr("click.edit", lambda text, **_kw: edited if text == SECTION else None)
    result = _invoke("kb", "review", "resolve", FLAG, "--edit", "--yes")
    assert result.exit_code == 0, result.output
    assert _sent(applied) == {
        "section_id": "dosing",
        "new_section_body": edited,
        "expected_revision": 3,
    }


@respx.mock
def test_an_unchanged_edit_applies_nothing(monkeypatch) -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    applied = respx.post(f"{BASE}/resolve").respond(200, json=RESOLVED)
    monkeypatch.setattr("click.edit", lambda text, **_kw: text)
    result = _invoke("kb", "review", "resolve", FLAG, "--edit", "--yes")
    assert result.exit_code == 1, result.output
    assert not applied.called
    assert "No changes" in result.output


@respx.mock
def test_a_card_table_flag_cannot_be_edited_as_text() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json={**CONFLICT, "section_kind": "card_table"})
    result = _invoke("kb", "review", "resolve", FLAG, "--edit", "--yes")
    assert result.exit_code != 0
    assert "row operations" in result.output


@respx.mock
def test_an_agent_refused_by_person_review_exits_six() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    respx.post(f"{BASE}/resolve").respond(
        403,
        json={
            "detail": {"code": "person_review_required", "message": "A person must accept."},
            "error": {
                "code": "person_review_required",
                "message": "A person must accept.",
                "details": {},
            },
        },
    )
    result = _invoke("kb", "review", "resolve", FLAG, "--option", "1", "--yes")
    assert result.exit_code == 6, result.output
    assert "a person, or a key with the proposals:accept (reviewer)" in " ".join(
        result.output.split()
    )
    assert "BEAKR_API_KEY" in result.output


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--option", "1", "--edit"),
        ("--outcome", "keep_both"),
    ],
)
def test_one_way_of_resolving_must_be_chosen(args) -> None:
    result = _invoke("kb", "review", "resolve", FLAG, *args, "--yes")
    assert result.exit_code != 0
    assert result.exit_code != 5


@respx.mock
def test_ls_lists_only_review_flags() -> None:
    listed = respx.get(f"{API}/v1/knowledge/wiki/proposals").respond(
        200,
        json={
            "data": [
                {
                    "id": FLAG,
                    "proposal_type": "review",
                    "page_title": "Compound X",
                    "summary": "IC50 disagrees",
                    "status": "pending",
                },
                {
                    "id": "0f4b6d82-3c5e-4a7f-8b19-2e6d8a0c4f51",
                    "proposal_type": "edit",
                    "page_title": "Other",
                    "summary": "An edit",
                    "status": "pending",
                },
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    result = _invoke("kb", "review", "ls", mode="compact")
    assert result.exit_code == 0, result.output
    assert listed.calls.last.request.url.params["proposal_type"] == "review"
    assert FLAG in result.stdout and "0f4b6d82" not in result.stdout


# --- the stdio MCP tools ---------------------------------------------------------


def _tool(name: str, **arguments) -> str:
    from tests import mcp_client

    result = asyncio.run(mcp_client.call_tool(name, arguments))
    return "".join(getattr(block, "text", "") for block in result.content)


@respx.mock
def test_the_stdio_tool_refuses_a_page_that_moved_on_before_sending_anything() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json={**CONFLICT, "current_revision": 5})
    drafts = respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    text = _tool("resolve_review", proposal_id=FLAG, option_id="A", expected_revision=3)
    assert "changed" in text and "review_options" in text
    assert not drafts.called


@respx.mock
def test_the_stdio_tool_sends_a_row_option_to_resolve_rows() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    applied = respx.post(f"{BASE}/resolve-rows").respond(200, json=RESOLVED)
    text = _tool("resolve_review", proposal_id=FLAG, option_id="2", expected_revision=3)
    assert _sent(applied) == {"option_id": "B", "expected_revision": 3}
    assert "Resolved review flag" in text


@respx.mock
def test_the_stdio_tool_says_a_person_must_resolve_it() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    respx.post(f"{BASE}/resolve").respond(
        403,
        json={
            "detail": {"code": "person_review_required", "message": "A person must accept."},
            "error": {
                "code": "person_review_required",
                "message": "A person must accept.",
                "details": {},
            },
        },
    )
    text = _tool("resolve_review", proposal_id=FLAG, option_id="A", expected_revision=3)
    assert "person_review_required" in text
    assert "nothing was applied" in text and "do not retry" in text
    assert "proposals:accept (reviewer)" in text


@respx.mock
def test_the_stdio_tool_lists_interview_answers_like_the_hosted_one() -> None:
    respx.get(BASE).respond(200, json={**PENDING_FLAG, "interview_answers": [DECISIVE_ANSWER]})
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    text = _tool("review_options", proposal_id=FLAG)
    lines = text.splitlines()
    assert "Interview answers:" in lines
    assert lines[-2].startswith(f"Apply one with resolve_review(proposal_id='{FLAG}'")
    assert lines[-1] == (
        "Applying the option an answer points to makes the row or section it leaves cite "
        "that answer."
    )


@respx.mock
def test_the_stdio_tool_says_how_a_decided_flag_was_decided() -> None:
    respx.get(BASE).respond(200, json=AUTO_APPLIED)
    drafts = respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    text = _tool("review_options", proposal_id=FLAG)
    assert text.startswith(f"Review flag {FLAG} is resolved: there is nothing to resolve.")
    assert "from the interview answer of Dana Reyes" in text
    assert not drafts.called


@respx.mock
def test_the_stdio_tool_says_the_resolved_row_cites_the_answer() -> None:
    respx.get(f"{BASE}/conflict").respond(200, json=CONFLICT)
    respx.post(f"{BASE}/resolutions").respond(200, json=_drafted())
    cited = [{"answered_by": "Dana Reyes", "file_name": "IC50 questions.md"}]
    respx.post(f"{BASE}/resolve-rows").respond(200, json={**RESOLVED, "cited_answers": cited})
    text = _tool("resolve_review", proposal_id=FLAG, option_id="B", expected_revision=3)
    assert text.endswith(" It now cites the interview answer of Dana Reyes (IC50 questions.md).")
