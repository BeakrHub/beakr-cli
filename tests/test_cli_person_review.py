"""Agent review (engine #1717, #1727): work that waits for a person.

A project can require a person to accept proposals, commit Knowledge and
approve an agent's uploads. A key or token that is not one is refused with
``403 person_review_required`` (or a Knowledge door refusal with that code):
the CLI exits 6 with its own hint, so a script tells "a person must do this"
apart from a bad credential (3). A held upload reads ``knowledge_status:
waiting_for_person``; ``beakr files approve`` sends it on
(``POST /v1/artifacts/{artifact_id}/knowledge/approve``) and file listings say
which files wait.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.errors import EXIT_PERSON_REVIEW
from beakr_cli.main import internal_app as app

API = "https://api.test"
PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
PROPOSAL = "1171c0a4-c839-486e-ba81-28b1a2445b80"
FILE = "3f6a1c2e-7b4d-4e9a-9c1f-2d8e5b7a6c40"
HASH = "a" * 64

_MESSAGE = (
    "This project requires a person to accept proposals and commit Knowledge, and this "
    "key or token is not one. Nothing was applied."
)
_FIX = (
    "Ask the person to review and accept it in the Beakr web app, or with a key they "
    "marked as their reviewer (scope proposals:accept)."
)


@pytest.fixture(autouse=True)
def api_env(isolated_home, monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _person_review_403() -> httpx.Response:
    return httpx.Response(
        403,
        json={
            "detail": {"code": "person_review_required", "message": _MESSAGE, "fix": _FIX},
            "error": {"code": "person_review_required", "message": _MESSAGE, "fix": _FIX},
        },
    )


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_exit_code_is_its_own() -> None:
    assert EXIT_PERSON_REVIEW == 6


@respx.mock
def test_proposals_accept_refused_for_a_person_exits_6_with_its_hint() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/accept").mock(
        return_value=_person_review_403()
    )

    result = CliRunner().invoke(
        app, ["kb", "proposals", "accept", PROPOSAL, "--reviewed-hash", HASH]
    )

    assert result.exit_code == EXIT_PERSON_REVIEW, result.output
    text = _flat(result.stderr)
    assert "requires a person to accept proposals" in text
    assert "proposals:accept scope (BEAKR_API_KEY)" in text
    # Not the generic missing-scope advice a 403 otherwise gets.
    assert "beakr keys create" not in text


@respx.mock
def test_proposals_accept_json_names_the_code() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/accept").mock(
        return_value=_person_review_403()
    )

    result = CliRunner().invoke(
        app, ["kb", "proposals", "accept", PROPOSAL, "--reviewed-hash", HASH, "--json"]
    )

    assert result.exit_code == EXIT_PERSON_REVIEW, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "person_review_required"
    assert error["exit_code"] == EXIT_PERSON_REVIEW


@respx.mock
def test_a_door_write_refused_for_a_person_exits_6() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": _MESSAGE}],
                "outcome": {"status": "error", "error_code": "person_review_required"},
            },
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "edit-rows",
            "HX-201-201",
            "--table",
            "facts",
            "--old-row",
            '{"attribute": "Enrollment", "value": "118"}',
            "--rationale",
            "Reconciled count.",
            "-P",
            PROJECT,
        ],
    )

    assert result.exit_code == EXIT_PERSON_REVIEW, result.output
    assert "proposals:accept scope" in _flat(result.stderr)


@respx.mock
def test_propose_create_accept_refused_for_a_person_exits_6() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Proposal id: x"}],
                "outcome": {"status": "success", "auto_approve": False},
                "proposal": {"id": PROPOSAL, "content_hash": HASH},
            },
        )
    )
    pending = {"id": PROPOSAL, "proposal_type": "create", "status": "pending", "content_hash": HASH}
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").mock(
        return_value=httpx.Response(200, json=pending)
    )
    respx.post(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/accept").mock(
        return_value=_person_review_403()
    )

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "propose",
            "create",
            "--title",
            "VB-77",
            "--sections",
            json.dumps([{"title": "Description", "body": "An NLRP3 inhibitor."}]),
            "-P",
            PROJECT,
            "--accept",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == EXIT_PERSON_REVIEW, result.output


@respx.mock
def test_files_approve_posts_the_approve_route() -> None:
    route = respx.post(f"{API}/v1/artifacts/{FILE}/knowledge/approve").mock(
        return_value=httpx.Response(
            200, json={"artifact_id": FILE, "queue_result": "queued", "knowledge_status": "queued"}
        )
    )

    result = CliRunner().invoke(app, ["files", "approve", FILE])

    assert result.exit_code == 0, result.output
    assert route.called
    assert f"beakr files show {FILE}" in result.stdout


@respx.mock
def test_files_approve_refused_for_a_person_exits_6() -> None:
    respx.post(f"{API}/v1/artifacts/{FILE}/knowledge/approve").mock(
        return_value=_person_review_403()
    )

    result = CliRunner().invoke(app, ["files", "approve", FILE])

    assert result.exit_code == EXIT_PERSON_REVIEW, result.output
    assert "approve agent uploads" in _flat(result.stderr)


@respx.mock
def test_files_approve_of_a_file_not_waiting_is_a_conflict_with_a_hint() -> None:
    respx.post(f"{API}/v1/artifacts/{FILE}/knowledge/approve").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": {
                    "code": "not_waiting",
                    "message": "This file is not waiting for a person to approve it.",
                }
            },
        )
    )

    result = CliRunner().invoke(app, ["files", "approve", FILE])

    assert result.exit_code == 5, result.output
    assert "beakr files show FILE_ID" in _flat(result.stderr)


def _files_page() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": [
                {
                    "id": FILE,
                    "title": "agent-notes.md",
                    "artifact_type": "document",
                    "knowledge_status": "waiting_for_person",
                    "updated_at": "2026-10-22T09:00:00Z",
                },
                {
                    "id": "f2",
                    "title": "memo.pdf",
                    "artifact_type": "document",
                    "knowledge_status": "created",
                    "updated_at": "2026-10-21T09:00:00Z",
                },
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )


@respx.mock
def test_files_ls_piped_names_each_files_knowledge_status() -> None:
    respx.get(f"{API}/v1/artifacts").mock(return_value=_files_page())

    result = CliRunner().invoke(app, ["files", "ls", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].split("\t")[:2] == [FILE, "agent-notes.md"]
    assert "knowledge=waiting_for_person" in lines[0].split("\t")
    assert "knowledge=created" in lines[1].split("\t")
    assert f"beakr files approve {FILE}" in result.stderr


@respx.mock
def test_files_show_says_a_file_waits_for_a_person(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_OUTPUT", "human")
    respx.get(f"{API}/v1/artifacts/{FILE}").mock(
        return_value=httpx.Response(
            200,
            json={"id": FILE, "title": "agent-notes.md", "knowledge_status": "waiting_for_person"},
        )
    )

    result = CliRunner().invoke(app, ["files", "show", FILE])

    assert result.exit_code == 0, result.output
    assert "waiting_for_person" in result.stdout
    assert f"beakr files approve {FILE}" in result.stdout
