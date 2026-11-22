"""The stdio ``knowledge_queue``, ``cancel_compiles`` and ``cancel_sync`` tools (ENG-2451).

The hosted server has the same three tools (beakr-engine
``beakr/api/mcp_remote/queue_tools.py``), and its
``tests/unit/test_mcp_every_tool_result_contract.py`` holds both servers to the
same answer. These pin what this server sends for each call, what it refuses
before sending anything, and the hosted text, line for line, for the answers that
contract test uses.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_cli import mcp_server, mcp_tools_platform, mcp_tools_projects, tool_surface
from tests import mcp_client

API = "https://api.test"
PROJECT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
SOURCE = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
JOB = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
CANCEL = "0b1e8a64-0c41-4d5e-9b0a-1f2e3d4c5b6a"
COMPILES = f"{API}/v1/projects/{PROJECT}/knowledge/compiles"
SYNC_CANCEL = f"{API}/v1/projects/{PROJECT}/sources/{SOURCE}/sync/cancel"

QUEUE = {
    "queued": 12,
    "running": 2,
    "last_cancel": {
        "cancel_id": CANCEL,
        "status": "cancelled",
        "done": 40,
        "total": 40,
        "summary": "Cancelled 40 of 40 queued compiles",
        "cancelled_by": "Dana",
        "cancelled_at": "2026-11-21T12:00:00Z",
    },
}
COMPILES_CANCELLED = {
    "cancel_id": CANCEL,
    "scope": "source",
    "status": "cancelling",
    "will_be_skipped": 40,
    "already_running": 2,
    "sync_stopped": True,
    "syncs_stopped": 1,
    "sync_job_id": JOB,
    "next_sync": "This source does not sync automatically.",
    "note": "Finished work is kept.",
}
SYNC_CANCELLED = {
    "sync_job_id": JOB,
    "status": "cancelling",
    "cancelled_by": "Dana",
    "next_sync": "This source does not sync automatically.",
    "note": "Items already synced are kept.",
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


# -- listing -----------------------------------------------------------------------------


async def test_the_tools_take_the_hosted_tools_arguments() -> None:
    listed = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    for name in ("knowledge_queue", "cancel_compiles"):
        schema = listed[name].input_schema
        assert set(schema["properties"]) == {"project", "attachment_id", "artifact_id"}, name
        assert schema["required"] == ["project"], name
    schema = listed["cancel_sync"].input_schema
    assert set(schema["properties"]) == {"project", "attachment_id"}
    assert schema["required"] == ["project", "attachment_id"]


async def test_the_cancels_are_marked_destructive_and_the_queue_read_only() -> None:
    listed = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    queue = listed["knowledge_queue"].annotations
    assert (queue.read_only_hint, queue.destructive_hint) == (True, False)
    for name in ("cancel_compiles", "cancel_sync"):
        hints = listed[name].annotations
        assert (
            hints.read_only_hint,
            hints.destructive_hint,
            hints.idempotent_hint,
            hints.open_world_hint,
        ) == (False, True, True, False), name
        assert listed[name].description == tool_surface.description(name)


# -- knowledge_queue ---------------------------------------------------------------------


@respx.mock
async def test_knowledge_queue_for_a_source_reads_its_compiles_and_answers_the_hosted_text():
    _project()
    route = respx.get(COMPILES).respond(json=QUEUE)
    text = await mcp_tools_projects.knowledge_queue(PROJECT, attachment_id=SOURCE)
    assert dict(route.calls.last.request.url.params) == {"attachment_id": SOURCE}
    assert text == (
        "Knowledge compiles for this source: 12 queued, 2 running.\n"
        "Last cancel: Cancelled 40 of 40 queued compiles (by Dana).\n"
        "cancel_compiles cancels the queued ones, after the person agrees; running ones finish."
    )


@respx.mock
async def test_knowledge_queue_for_the_project_with_nothing_queued_is_one_line():
    _project()
    route = respx.get(COMPILES).respond(json={"queued": 0, "running": 0, "last_cancel": None})
    text = await mcp_tools_projects.knowledge_queue(PROJECT)
    assert not route.calls.last.request.url.params
    assert text == "Knowledge compiles for this project: 0 queued, 0 running."


@respx.mock
async def test_knowledge_queue_for_a_file_sends_its_artifact_id():
    _project()
    route = respx.get(COMPILES).respond(json={"queued": 1, "running": 0, "last_cancel": None})
    text = await mcp_tools_projects.knowledge_queue(PROJECT, artifact_id="f1")
    assert dict(route.calls.last.request.url.params) == {"artifact_id": "f1"}
    assert text.startswith("Knowledge compiles for this source: 1 queued, 0 running.")


@pytest.mark.parametrize(
    "tool", [mcp_tools_projects.knowledge_queue, mcp_tools_projects.cancel_compiles]
)
@respx.mock
async def test_a_source_and_a_file_together_are_refused_before_anything_is_sent(tool):
    result = await tool(PROJECT, attachment_id=SOURCE, artifact_id="f1")
    assert result == {
        "status": "error",
        "error_code": "invalid_arguments",
        "message": "Name one source: attachment_id or artifact_id, not both.",
        "fix": "Pass one of them, or neither for the whole project.",
    }
    assert not respx.calls


# -- cancel_compiles ---------------------------------------------------------------------


@respx.mock
async def test_cancel_compiles_for_a_source_answers_the_hosted_text():
    _project()
    route = respx.post(f"{COMPILES}/cancel").respond(202, json=COMPILES_CANCELLED)
    text = await mcp_tools_projects.cancel_compiles(PROJECT, attachment_id=SOURCE)
    assert json.loads(route.calls.last.request.content) == {"attachment_id": SOURCE}
    assert text == (
        "40 queued compiles will not run (cancelling). 2 already running will finish.\n"
        "Also cancelled 1 running sync(s): items they had not handed off will not queue.\n"
        "This source does not sync automatically.\n"
        "Finished work is kept.\n"
        f"cancel_id: {CANCEL}. knowledge_queue reports progress."
    )


@respx.mock
async def test_cancel_compiles_for_the_project_sends_an_empty_body():
    _project()
    route = respx.post(f"{COMPILES}/cancel").respond(
        202,
        json={
            **COMPILES_CANCELLED,
            "scope": "project",
            "status": "cancelled",
            "sync_stopped": False,
            "syncs_stopped": 0,
            "sync_job_id": None,
            "next_sync": "",
            "note": "",
        },
    )
    text = await mcp_tools_projects.cancel_compiles(PROJECT)
    assert json.loads(route.calls.last.request.content) == {}
    assert text == (
        "40 queued compiles will not run (cancelled). 2 already running will finish.\n"
        f"cancel_id: {CANCEL}. knowledge_queue reports progress."
    )


# -- cancel_sync -------------------------------------------------------------------------


@respx.mock
async def test_cancel_sync_posts_no_body_and_answers_the_hosted_text():
    _project()
    route = respx.post(SYNC_CANCEL).respond(json=SYNC_CANCELLED)
    text = await mcp_tools_projects.cancel_sync(PROJECT, SOURCE)
    assert route.call_count == 1
    assert route.calls.last.request.content in (b"", b"null")
    assert text == (
        f"Cancelling the sync (job {JOB}): listing stops at its next step and items not yet "
        "started are skipped. Compiles already queued still run; cancel_compiles with this "
        "attachment_id cancels them.\n"
        "This source does not sync automatically.\n"
        "Items already synced are kept."
    )


@respx.mock
async def test_cancel_sync_without_a_source_is_refused_before_anything_is_sent():
    result = await mcp_tools_projects.cancel_sync(PROJECT, "  ")
    assert result == {
        "status": "error",
        "error_code": "invalid_arguments",
        "message": "Name the source: its attachment_id.",
        "fix": "Pass attachment_id, the connected source's attachment id in the project.",
    }
    assert not respx.calls


@respx.mock
async def test_cancel_sync_with_nothing_running_is_a_refusal_the_agent_can_read():
    _project()
    respx.post(SYNC_CANCEL).respond(
        409,
        json={
            "error": {"code": "no_running_sync", "message": "No sync is running for this source."}
        },
    )
    result = await mcp_client.call_tool(
        "cancel_sync", {"project": PROJECT, "attachment_id": SOURCE}
    )
    assert result.is_error
    sent = json.loads(result.content[0].text)
    assert "No sync is running for this source." in json.dumps(sent)
