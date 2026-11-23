"""The stdio ``draft_review_interview`` tool.

The hosted server has the same tool (beakr-engine
``beakr/api/mcp_remote/review_interview_tools.py``), and its
``tests/unit/test_mcp_every_tool_result_contract.py`` holds both servers to the
same answer. These pin the arguments the tool takes (the hosted tool's:
project, flags, kinds, title), what it sends, and the hosted text, line for line.
It drafts only: it takes no participant, so no link is made and nothing is sent.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_cli import mcp_server, mcp_tools_platform, mcp_tools_projects, tool_surface
from beakr_cli.review_interview import review_interview_text
from tests import mcp_client

API = "https://api.test"
PROJECT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
FLAG = "6f1c0e7a-2b3d-4c5e-8f90-a1b2c3d4e5f6"
SKIPPED = "0a9b8c7d-6e5f-4a3b-8c2d-1e0f9a8b7c6d"
INTERVIEW = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
FROM_REVIEWS = f"{API}/v1/projects/{PROJECT}/interviews/from-reviews"
EDIT_URL = f"https://app.example/interviews/send?edit={INTERVIEW}"

DRAFTED = {
    "interview": {"id": INTERVIEW, "title": "Settle the Lot 42 questions", "links": []},
    "questions": [
        {
            "number": 1,
            "question": "Which release date of Lot 42 is right, 3 May or 5 May?",
            "proposal_id": FLAG,
            "kind": "contradiction",
            "page_title": "Lot 42",
            "reason": "Two records give different release dates.",
        }
    ],
    "skipped": [{"proposal_id": SKIPPED, "reason": "not_open"}],
    "questions_written_by": "model",
    "edit_url": EDIT_URL,
    "empty_reason": None,
    "message": None,
}


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
    tool = listed["draft_review_interview"]
    schema = tool.input_schema
    # The self-hosted line adds confirm_lock where the shared surface lists the tool
    # (tests/test_lock_confirm.py); the hosted tool's own arguments are the rest.
    assert set(schema["properties"]) - {"confirm_lock"} == {"project", "flags", "kinds", "title"}
    assert schema["required"] == ["project"]
    assert tool.description == tool_surface.description("draft_review_interview")
    hints = tool.annotations
    assert (
        hints.read_only_hint,
        hints.destructive_hint,
        hints.idempotent_hint,
        hints.open_world_hint,
    ) == (False, False, False, False)


@respx.mock
async def test_a_draft_answers_the_hosted_text() -> None:
    _project()
    route = respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    text = await mcp_tools_projects.draft_review_interview(
        PROJECT, kinds=["contradiction", "ambiguity"]
    )
    assert json.loads(route.calls.last.request.content) == {"kinds": ["contradiction", "ambiguity"]}
    assert text == (
        f'Drafted interview "Settle the Lot 42 questions" (id {INTERVIEW}) with 1 question(s), '
        "one per review flag. Nothing was sent.\n"
        "1. Which release date of Lot 42 is right, 3 May or 5 May?\n"
        f"   Flag {FLAG} (contradiction) on Lot 42: Two records give different release dates.\n"
        f"Skipped: {SKIPPED} (not_open).\n"
        f"The person edits and sends it in Beakr: {EDIT_URL}. Answers come back on each flag "
        "as suggestions; a person applies them."
    )


@respx.mock
async def test_flags_kinds_and_title_are_sent_as_the_hosted_tool_sends_them() -> None:
    _project()
    route = respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    await mcp_tools_projects.draft_review_interview(
        PROJECT,
        flags=[FLAG, SKIPPED],
        kinds=["other"],
        title=" Lot 42 ",
    )
    assert json.loads(route.calls.last.request.content) == {
        "proposal_ids": [FLAG, SKIPPED],
        "kinds": ["other"],
        "title": "Lot 42",
    }


async def test_the_tool_takes_no_participant_so_it_never_makes_a_link() -> None:
    listed = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    properties = listed["draft_review_interview"].input_schema["properties"]
    assert "participant" not in properties
    assert "link_expires_in_days" not in properties


@respx.mock
async def test_nothing_to_ask_says_why() -> None:
    _project()
    respx.post(FROM_REVIEWS).respond(
        200,
        json={
            "interview": None,
            "questions": [],
            "skipped": [{"proposal_id": SKIPPED, "reason": "not_found"}],
            "questions_written_by": None,
            "edit_url": None,
            "empty_reason": "no_askable_flags",
            "message": "None of the named flags is open in this project.",
        },
    )
    text = await mcp_tools_projects.draft_review_interview(PROJECT, flags=[SKIPPED])
    assert text == (
        "Nothing drafted: None of the named flags is open in this project.\n"
        f"Skipped: {SKIPPED} (not_found)."
    )


def test_an_answer_without_a_message_names_the_empty_reason() -> None:
    assert (
        review_interview_text({"interview": None, "empty_reason": "no_open_flags"})
        == "Nothing drafted: no_open_flags"
    )


@respx.mock
async def test_an_unknown_kind_is_refused_before_anything_is_sent() -> None:
    result = await mcp_client.call_tool(
        "draft_review_interview", {"project": PROJECT, "kinds": ["all"]}
    )
    assert result.is_error
    assert not respx.calls


@respx.mock
async def test_a_refusal_reaches_the_agent() -> None:
    _project()
    respx.post(FROM_REVIEWS).respond(
        403, json={"error": {"code": "insufficient_scope", "message": "Needs interviews:write."}}
    )
    result = await mcp_client.call_tool("draft_review_interview", {"project": PROJECT})
    assert result.is_error
    assert "Needs interviews:write." in result.content[0].text
