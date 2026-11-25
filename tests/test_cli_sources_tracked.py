"""``beakr sources tracked`` and what ``beakr sources status`` says about each item.

Octant (sandbox, 23 Nov 2026) asked for the reason on every skipped or failed
item wherever a sync is reported. Reasons Knowledge decides after reading an
item (``nothing_to_add`` and the other compile-time skips) reached only the web
app's Tracked items. The engine now lists tracked items to a key
(``GET .../sources/{id}/tracked-items``) and puts ``outcomes`` on the sync
status; these tests pin what the CLI sends and how the answers read.
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


TRACKED = f"{SOURCES}/s1/tracked-items"
COMPILES = f"{API}/v1/projects/{PROJECT}/knowledge/compiles"
runner = CliRunner()

LUNCH = {
    "external_id": "conn:t1",
    "title": "Lunch order",
    "kind": "thread",
    "state": "skipped",
    "reason_code": "nothing_to_add",
    "reason": "A lunch order; nothing for the project.",
}
PROTOCOL = {"external_id": "conn:t2", "title": "Protocol", "kind": "thread", "state": "ingested"}
COUNTS = {"total": 3, "pending": 1, "ingested": 1, "skipped": 1, "removed": 0}


def _page(rows: list[dict], *, cursor: str | None = None, matched: int | None = None) -> dict:
    return {
        "data": rows,
        "next_cursor": cursor,
        "has_more": cursor is not None,
        "provider": "gmail",
        "kind": "thread",
        "counts": COUNTS,
        "matched": len(rows) if matched is None else matched,
    }


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def at_terminal(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


# -- sources tracked ------------------------------------------------------------------


@respx.mock
def test_tracked_prints_the_counts_and_each_item_with_why_it_was_set_aside(at_terminal):
    route = respx.get(TRACKED).respond(json=_page([LUNCH, PROTOCOL], matched=3))
    result = runner.invoke(app, ["sources", "tracked", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert dict(route.calls.last.request.url.params) == {"limit": "50"}
    lines = result.output.splitlines()
    assert lines[0] == "3 tracked item(s) from gmail: 1 ingested, 1 skipped, 1 pending, 0 removed."
    assert (
        "  Lunch order  [skipped]  A lunch order; nothing for the project. (nothing_to_add)"
        in lines
    )
    assert "  Protocol  [ingested]" in lines


@respx.mock
def test_skipped_and_the_filters_reach_the_route():
    route = respx.get(TRACKED).respond(json=_page([LUNCH], matched=1))
    result = runner.invoke(
        app,
        [
            "sources",
            "tracked",
            "s1",
            "-P",
            PROJECT,
            "--skipped",
            "--reason-code",
            "nothing_to_add",
            "--search",
            "lunch",
            "--since",
            "2026-11-23T10:00:00+00:00",
            "--limit",
            "10",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert dict(route.calls.last.request.url.params) == {
        "limit": "10",
        "state": "skipped",
        "reason_code": "nothing_to_add",
        "search": "lunch",
        "since": "2026-11-23T10:00:00+00:00",
    }
    assert json.loads(result.stdout) == _page([LUNCH], matched=1)


@respx.mock
def test_skipped_beside_another_state_is_refused_before_anything_is_sent():
    route = respx.get(TRACKED).respond(json=_page([]))
    result = runner.invoke(
        app, ["sources", "tracked", "s1", "-P", PROJECT, "--skipped", "--state", "ingested"]
    )
    assert result.exit_code == 2
    assert not route.called
    bad = runner.invoke(
        app, ["sources", "tracked", "s1", "-P", PROJECT, "--state", "unprocessable"]
    )
    assert bad.exit_code == 2
    assert not route.called


@respx.mock
def test_all_follows_every_page_and_json_is_one_list():
    route = respx.get(TRACKED).mock(
        side_effect=[
            respx.MockResponse(200, json=_page([LUNCH], cursor="c2")),
            respx.MockResponse(200, json=_page([PROTOCOL])),
        ]
    )
    result = runner.invoke(app, ["sources", "tracked", "s1", "-P", PROJECT, "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 2
    assert route.calls.last.request.url.params["cursor"] == "c2"
    body = json.loads(result.stdout)
    assert [row["external_id"] for row in body["data"]] == ["conn:t1", "conn:t2"]
    assert body["has_more"] is False and body["next_cursor"] is None


@respx.mock
def test_one_page_piped_is_a_line_per_item_and_names_the_next_page():
    respx.get(TRACKED).respond(json=_page([LUNCH], cursor="c2"))
    result = runner.invoke(app, ["sources", "tracked", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].split("\t") == [
        "conn:t1",
        "skipped",
        "nothing_to_add",
        "Lunch order",
    ]
    assert "next: beakr sources tracked s1 --cursor c2" in result.stdout


@respx.mock
def test_tracked_with_nothing_matching_says_so(at_terminal):
    respx.get(TRACKED).respond(json=_page([], matched=0))
    result = runner.invoke(app, ["sources", "tracked", "s1", "-P", PROJECT, "--search", "x"])
    assert result.exit_code == 0, result.output
    assert "0 match the filters." in result.output
    assert "No tracked items match." in result.output


# -- sources status: what became of each item -------------------------------------------

OUTCOMES = {
    "since": "2026-11-23T10:00:00+00:00",
    "until": None,
    "total": 4,
    "counts": {
        "ingested": 2,
        "skipped": 1,
        "pending": 0,
        "removed": 0,
        "failed": 1,
        "cancelled": 0,
    },
    "reasons": [
        {"outcome": "skipped", "reason_code": "nothing_to_add", "count": 1},
        {"outcome": "failed", "reason_code": "permission_denied", "count": 1},
    ],
    "items": [
        {
            "external_id": "conn:t1",
            "title": "Lunch order",
            "outcome": "skipped",
            "reason_code": "nothing_to_add",
            "reason": "A lunch order; nothing for the project.",
        },
        {
            "external_id": None,
            "title": "Locked.pdf",
            "outcome": "failed",
            "reason_code": "permission_denied",
            "reason": "No permission.",
        },
        {
            "external_id": "conn:t2",
            "title": "Protocol",
            "outcome": "ingested",
            "reason_code": None,
            "reason": None,
        },
    ],
    "items_limit": 25,
    "has_more": False,
    "tracked_items_path": "/v1/projects/p1/sources/s1/tracked-items?since=x",
}
STATUS = {
    "status": "completed",
    "files_total": 2,
    "files_completed": 1,
    "files_failed": 1,
    "files": [{"name": "gmail: 3 items", "status": "completed", "outcome": "ingested"}],
    "outcomes": OUTCOMES,
}


@respx.mock
def test_status_says_what_knowledge_set_aside_and_why(at_terminal):
    _listed()
    respx.get(f"{SOURCES}/s1/sync-status").respond(json=STATUS)
    respx.get(COMPILES).respond(json={"queued": 0, "running": 0, "last_cancel": None})
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "Items: 2 ingested, 1 skipped, 1 failed." in lines
    assert "Why: nothing_to_add 1, permission_denied 1." in lines
    assert (
        "  Lunch order  [skipped]  A lunch order; nothing for the project. (nothing_to_add)"
        in lines
    )
    assert "  Locked.pdf  [failed]  No permission. (permission_denied)" in lines
    assert not any("Protocol" in line for line in lines)
    assert "Every item and why" not in result.output


@respx.mock
def test_status_points_at_tracked_when_more_items_were_set_aside_than_it_names(at_terminal):
    _listed()
    many = {**OUTCOMES, "counts": {**OUTCOMES["counts"], "pending": 40}}
    respx.get(f"{SOURCES}/s1/sync-status").respond(json={**STATUS, "outcomes": many})
    respx.get(COMPILES).respond(json={"queued": 0, "running": 0, "last_cancel": None})
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert "Every item and why: beakr sources tracked s1" in result.output


@respx.mock
def test_status_piped_counts_each_outcome():
    _listed()
    respx.get(f"{SOURCES}/s1/sync-status").respond(json=STATUS)
    respx.get(COMPILES).respond(json={"queued": 0, "running": 0, "last_cancel": None})
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    fields = result.stdout.splitlines()[0].split("\t")
    assert "items_skipped=1" in fields and "items_failed=1" in fields
    assert "items_ingested=2" in fields


# -- failed items and the history import's status (R6, R12; engine #3292) ---------------

ASSAY = {
    "external_id": "conn:t3",
    "title": "Assay plan",
    "kind": "thread",
    "state": "failed",
    "reason_code": "compile_failed",
    "reason": "The compile did not finish; the next sync sends it again.",
}


@respx.mock
def test_tracked_lists_failed_items_and_counts_them(monkeypatch):
    counts = {**COUNTS, "total": 4, "failed": 1}
    route = respx.get(TRACKED).respond(200, json={**_page([ASSAY]), "counts": counts})
    result = runner.invoke(app, ["sources", "tracked", "s1", "--state", "failed", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert dict(route.calls.last.request.url.params)["state"] == "failed"
    assert result.stdout.splitlines() == ["conn:t3\tfailed\tcompile_failed\tAssay plan"]
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["sources", "tracked", "s1", "-P", PROJECT])
    said = " ".join(result.output.split())
    assert "1 ingested, 1 skipped, 1 failed, 1 pending, 0 removed." in said


HISTORY = f"{SOURCES}/s1/history-import/status"
FINISHED = {
    "in_knowledge": 492,
    "skipped": 93,
    "failed": 26,
    "left": 0,
    "removed": 3,
    "of": 611,
    "state": "finished",
    "finished_at": "2026-11-24T03:00:00Z",
    "summary": "Import finished on 24 Nov 2026: 492 in Knowledge, 93 skipped, 26 failed, 0 left",
    "skipped_reasons": [{"reason_code": "nothing_to_add", "count": 90}],
    "failed_reasons": [
        {"reason_code": "compile_failed", "count": 20},
        {"reason_code": "import_gave_up", "count": 6},
    ],
    "left_reasons": [],
    "followup": {"status": "finished", "attempts": 2, "last_sent": 4, "next_at": None},
}


@respx.mock
def test_import_status_says_how_the_last_import_ended(monkeypatch):
    respx.get(HISTORY).respond(
        200,
        json={
            "attachment_id": "s1",
            "provider": "gdrive:nango",
            "supported": True,
            "status": "idle",
            "retrying": False,
            "request": None,
            "progress": None,
            "outcome": None,
            "last_import": {"requested_at": "2026-11-23T20:00:00Z"},
            "last_import_cancelled": False,
            "last_outcome": FINISHED,
        },
    )
    result = runner.invoke(app, ["sources", "import-status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "s1\tstatus=idle\tsupported=true\tlast_state=finished\tlast_in_knowledge=492"
        "\tlast_skipped=93\tlast_failed=26\tlast_left=0\tlast_removed=3"
    ]
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["sources", "import-status", "s1", "-P", PROJECT])
    assert result.output.splitlines() == [
        "No history import is waiting or running.",
        "Last import:",
        "  Import finished on 24 Nov 2026: 492 in Knowledge, 93 skipped, 26 failed, 0 left.",
        "    Skipped: nothing_to_add 90",
        "    Failed: compile_failed 20, import_gave_up 6",
        "    Follow-up finished: sent the leftovers again 2 time(s), 4 item(s) last time.",
    ]
    data = json.loads(
        runner.invoke(app, ["sources", "import-status", "s1", "-P", PROJECT, "--json"]).stdout
    )
    assert data["last_outcome"]["failed_reasons"][0]["reason_code"] == "compile_failed"


@respx.mock
def test_import_status_of_a_running_import_shows_its_progress(monkeypatch):
    working = {**FINISHED, "state": "working", "finished_at": None, "left": 12, "followup": None}
    working["summary"] = "Importing: 492 in Knowledge, 93 skipped, 26 failed, 12 left"
    working["left_reasons"] = [{"reason": "compiling", "label": "being compiled", "count": 12}]
    respx.get(HISTORY).respond(
        200,
        json={
            "provider": "gdrive:nango",
            "supported": True,
            "status": "running",
            "retrying": True,
            "progress": {"tracked": 623, "brought_in": 611, "compiled": 492, "unprocessable": 93},
            "outcome": working,
            "last_outcome": None,
        },
    )
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["sources", "import-status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:3] == [
        "History import running (a sync stopped partway; the next sync runs it again).",
        "  623 tracked, 611 read, 492 compiled, 93 set aside.",
        "Importing: 492 in Knowledge, 93 skipped, 26 failed, 12 left.",
    ]
    assert "  Left: compiling 12" in result.output.splitlines()


@respx.mock
def test_import_status_says_when_a_source_takes_no_import():
    respx.get(HISTORY).respond(
        200,
        json={
            "provider": "notion:nango",
            "supported": False,
            "unsupported_reason": "Notion pages have no history to import",
            "status": "idle",
        },
    )
    result = runner.invoke(app, ["sources", "import-status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["s1\tstatus=idle\tsupported=false"]


# -- recompile (R4; engine #3298) ----------------------------------------------------------

RECOMPILE = f"{SOURCES}/s1/recompile"
SENT = {
    "mode": "retry",
    "dry_run": False,
    "requested": 2,
    "in_knowledge_left": 1,
    "over_cap": 0,
    "not_found": ["conn:gone"],
    "sync": "started",
    "sync_job_id": "11111111-2222-3333-4444-555555555555",
}


@respx.mock
def test_recompile_sends_the_named_items_and_says_what_went(monkeypatch):
    route = respx.post(RECOMPILE).respond(200, json=SENT)
    argv = ["sources", "recompile", "s1", "--items", "conn:t1,conn:t2", "--items", "conn:gone"]
    result = runner.invoke(app, [*argv, "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "mode": "retry",
        "dry_run": False,
        "external_ids": ["conn:t1", "conn:t2", "conn:gone"],
    }
    assert result.stdout.splitlines() == [
        "s1\tmode=retry\tdry_run=false\trequested=2\tin_knowledge_left=1\tover_cap=0"
        "\tnot_found=1\tsync=started\tsync_job_id=11111111-2222-3333-4444-555555555555",
        "next: beakr sources status s1",
    ]
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, [*argv, "-P", PROJECT])
    said = " ".join(result.output.split())
    assert "Sent 2 item(s) of s1 to Knowledge again (retry)." in said
    assert "1 already in Knowledge at their current version were left alone" in said
    assert "Not tracked by this source, nothing sent: conn:gone." in said
    assert "A sync reading them has started (job 11111111-2222-3333-4444-555555555555)." in said


@respx.mock
def test_recompile_all_in_full_as_a_dry_run(monkeypatch):
    route = respx.post(RECOMPILE).respond(
        200,
        json={
            **SENT,
            "mode": "full",
            "dry_run": True,
            "requested": 500,
            "over_cap": 23,
            "in_knowledge_left": 0,
            "not_found": [],
            "sync": "not_started",
            "sync_job_id": None,
        },
    )
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(
        app, ["sources", "recompile", "s1", "--all", "--full", "--dry-run", "-P", PROJECT]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "mode": "full",
        "dry_run": True,
        "all": True,
    }
    said = " ".join(result.output.split())
    assert "Would send 500 item(s) of s1 to Knowledge again (full)." in said
    assert "23 more are past the 500-item limit" in said
    assert "sync" not in said.lower().replace("beakr sources status", "")


@pytest.mark.parametrize("argv", [[], ["--items", "conn:t1", "--all"], ["--items", " , "]])
def test_recompile_needs_items_or_all(argv):
    result = runner.invoke(app, ["sources", "recompile", "s1", *argv, "-P", PROJECT])
    assert result.exit_code == 2
    assert "--items" in result.output and "--all" in result.output


@respx.mock
def test_a_recompile_cooldown_is_said_as_the_engine_says_it():
    respx.post(RECOMPILE).respond(
        429,
        headers={"Retry-After": "540"},
        json={
            "error": {
                "code": "recompile_cooldown",
                "message": "This source was sent to Knowledge again less than 10 minutes ago.",
            }
        },
    )
    result = runner.invoke(app, ["sources", "recompile", "s1", "--all", "-P", PROJECT, "--json"])
    assert result.exit_code != 0
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "recompile_cooldown"
    assert "less than 10 minutes ago" in error["message"]


@respx.mock
def test_tracked_says_when_an_older_version_is_in_knowledge(monkeypatch):
    updating = {**ASSAY, "state": "pending", "reason_code": None, "reason": None}
    respx.get(TRACKED).respond(200, json=_page([{**updating, "older_version_in_knowledge": True}]))
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["sources", "tracked", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert "Assay plan  [pending]  (an older version is in Knowledge)" in result.output
