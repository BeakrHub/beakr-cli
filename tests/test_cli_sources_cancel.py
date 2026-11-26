"""``beakr sources cancel-sync`` and ``beakr sources status`` (ENG-2451).

Max asked (2026-11-21) for a way to stop what Beakr is pulling and compiling:
a running connector sync (Notion follow-up syncs included) and the Knowledge
compiles still queued for a project or one source. These tests pin what each
command sends to the engine's documented routes (beakr-engine #3078, shapes
``SyncCancelOut`` and ``CompileQueueOut``), how the
answers read, and the exit codes a script branches on.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "p1"
SOURCES = f"{API}/v1/projects/{PROJECT}/sources"


def _listed(*rows: dict) -> None:
    """The project's sources as ``ls`` lists them; ``status`` reads its source's health there."""
    respx.get(SOURCES).respond(
        200, json={"data": list(rows), "has_more": False, "next_cursor": None}
    )


COMPILES = f"{API}/v1/projects/{PROJECT}/knowledge/compiles"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def at_terminal(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _sync_cancelled(**fields) -> dict:
    return {
        "sync_job_id": "j1",
        "status": "cancelling",
        "cancelled_by": "Ada Lovelace",
        "next_sync": "Tonight's nightly sync, or `Sync now`.",
        "note": "Items already being read finish; the rest are skipped.",
        **fields,
    }


def _queue(**fields) -> dict:
    return {"queued": 12, "running": 1, "last_cancel": None, **fields}


# -- sources cancel-sync --------------------------------------------------------------


@respx.mock
def test_cancel_sync_posts_no_body_and_says_what_happens(at_terminal):
    route = respx.post(f"{SOURCES}/s1/sync/cancel").respond(200, json=_sync_cancelled())
    result = runner.invoke(app, ["sources", "cancel-sync", "s1", "-P", PROJECT, "--yes"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.content in (b"", b"null")
    assert "Stopping the sync of s1 (job j1)." in result.output
    assert "Items already being read finish" in result.output
    assert "Next sync: Tonight's nightly sync" in result.output


@respx.mock
def test_cancel_sync_piped_is_one_line_and_json_is_the_answer():
    respx.post(f"{SOURCES}/s1/sync/cancel").respond(200, json=_sync_cancelled())
    result = runner.invoke(app, ["sources", "cancel-sync", "s1", "-P", PROJECT, "--yes"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].split("\t") == ["s1", "cancelling", "job=j1"]
    assert "next: beakr sources status s1 -P p1" in result.stdout
    result = runner.invoke(app, ["sources", "cancel-sync", "s1", "-P", PROJECT, "-y", "--json"])
    assert json.loads(result.stdout) == _sync_cancelled()


@respx.mock
def test_cancel_sync_without_yes_off_a_terminal_sends_nothing():
    route = respx.post(f"{SOURCES}/s1/sync/cancel").respond(200, json=_sync_cancelled())
    result = runner.invoke(app, ["sources", "cancel-sync", "s1", "-P", PROJECT])
    assert result.exit_code == 2
    assert "--yes" in result.output
    assert not route.called


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": "no_running_sync", "message": "No sync is running for this source."}},
        {"detail": {"code": "no_running_sync", "message": "No sync is running for this source."}},
    ],
    ids=["envelope", "detail"],
)
@respx.mock
def test_cancel_sync_with_nothing_running_is_a_conflict_that_says_so(body):
    respx.post(f"{SOURCES}/s1/sync/cancel").respond(409, json=body)
    result = runner.invoke(app, ["sources", "cancel-sync", "s1", "-P", PROJECT, "--yes"])
    assert result.exit_code == 5, result.output
    said = " ".join(result.output.split())
    assert "No sync is running for this source. (HTTP 409, no_running_sync)" in said
    assert "Nothing to stop. `beakr sources status s1 -P p1` shows its last sync." in said


@pytest.mark.parametrize(("status", "exit_code"), [(403, 3), (404, 4)])
@respx.mock
def test_cancel_sync_refusals_exit_with_their_codes(status, exit_code):
    respx.post(f"{SOURCES}/s1/sync/cancel").respond(status, json={"detail": "no"})
    result = runner.invoke(app, ["sources", "cancel-sync", "s1", "-P", PROJECT, "--yes"])
    assert result.exit_code == exit_code, result.output


@respx.mock
def test_cancel_sync_encodes_the_source_as_one_path_segment():
    route = respx.post(f"{SOURCES}/a%2Fb/sync/cancel").respond(200, json=_sync_cancelled())
    result = runner.invoke(app, ["sources", "cancel-sync", "a/b", "-P", PROJECT, "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


# -- sources status --------------------------------------------------------------------


@respx.mock
def test_status_of_a_syncing_source_shows_its_compiles_and_how_to_stop_it(at_terminal):
    _listed()
    respx.get(f"{SOURCES}/s1/sync-status").respond(
        200,
        json={"status": "syncing", "files_total": 10, "files_completed": 4, "files_failed": 0},
    )
    queue = respx.get(COMPILES, params={"attachment_id": "s1"}).respond(200, json=_queue())
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert queue.called
    assert "Sync syncing: 4/10 files done, 0 failed." in result.output
    assert "12 compiles queued and 1 running for this source." in result.output
    assert "beakr sources cancel-sync s1" in result.output


@respx.mock
def test_status_piped_adds_the_compile_counts_and_json_carries_them():
    _listed()
    respx.get(f"{SOURCES}/s1/sync-status").respond(
        200,
        json={"status": "syncing", "files_total": 10, "files_completed": 4, "files_failed": 0},
    )
    respx.get(COMPILES).respond(200, json=_queue())
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    fields = result.stdout.splitlines()[0].split("\t")
    assert fields[:2] == ["s1", "syncing"]
    assert fields[-2:] == ["compiles_queued=12", "compiles_running=1"]
    assert "next: beakr sources cancel-sync s1 -P p1" in result.stdout
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT, "--json"])
    assert json.loads(result.stdout)["compiles"] == _queue()


@respx.mock
def test_status_of_a_finished_sync_offers_no_cancel(at_terminal):
    _listed()
    respx.get(f"{SOURCES}/s1/sync-status").respond(
        200,
        json={"status": "completed", "files_total": 3, "files_completed": 3, "files_failed": 0},
    )
    respx.get(COMPILES).respond(200, json=_queue(queued=0, running=0))
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert "No compiles queued or running for this source." in result.output
    assert "cancel-sync" not in result.output


@respx.mock
def test_status_without_a_source_lists_the_syncing_ones(at_terminal):
    respx.get(SOURCES).respond(
        200,
        json={
            "data": [
                {
                    "id": "s1",
                    "external_item_name": "Handbook",
                    "provider": "notion:nango",
                    "external_item_type": "page",
                    "sync_status": "syncing",
                },
                {
                    "id": "s2",
                    "external_item_name": "Assays",
                    "provider": "gdrive:nango",
                    "external_item_type": "folder",
                    "sync_status": "completed",
                },
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    result = runner.invoke(app, ["sources", "status", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert "Syncing now (1)" in result.output
    assert "s1" in result.output and "s2" not in result.output
    assert "beakr sources cancel-sync <id>" in result.output


@respx.mock
def test_status_without_a_source_says_when_none_is_syncing():
    respx.get(SOURCES).respond(200, json={"data": [], "has_more": False, "next_cursor": None})
    result = runner.invoke(app, ["sources", "status", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert "No source is syncing." in result.stdout
    result = runner.invoke(app, ["sources", "status", "-P", PROJECT, "--json"])
    assert json.loads(result.stdout) == []


@respx.mock
def test_status_of_a_cancelled_sync_says_how_far_it_got(at_terminal):
    _listed()
    respx.get(f"{SOURCES}/s1/sync-status").respond(
        200,
        json={
            "status": "cancelled",
            "files_total": 40,
            "files_completed": 12,
            "files_failed": 0,
            "files_cancelled": 28,
            "cancel": {
                "by_name": "Dana",
                "at": "2026-11-21T12:00:00Z",
                "items_done": 12,
                "items_total": 40,
                "summary": "Cancelled at 12 of 40",
            },
        },
    )
    respx.get(COMPILES).respond(200, json=_queue(queued=0, running=0))
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    said = " ".join(result.output.split())
    assert "Sync cancelled: 12/40 files done, 0 failed, 28 skipped by the cancel." in said
    assert "Cancelled at 12 of 40 by Dana at 2026-11-21T12:00:00Z." in said
    assert "cancel-sync" not in said


@respx.mock
def test_status_piped_counts_what_the_cancel_skipped():
    _listed()
    respx.get(f"{SOURCES}/s1/sync-status").respond(
        200,
        json={
            "status": "cancelling",
            "files_total": 40,
            "files_completed": 12,
            "files_failed": 0,
            "files_cancelled": 20,
        },
    )
    respx.get(COMPILES).respond(404, json={"detail": "Not Found"})
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].split("\t")[1:] == [
        "cancelling",
        "files_total=40",
        "files_completed=12",
        "files_failed=0",
        "files_cancelled=20",
    ]
