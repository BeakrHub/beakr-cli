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
    many = {**OUTCOMES, "counts": {**OUTCOMES["counts"], "pending": 40}}
    respx.get(f"{SOURCES}/s1/sync-status").respond(json={**STATUS, "outcomes": many})
    respx.get(COMPILES).respond(json={"queued": 0, "running": 0, "last_cancel": None})
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert "Every item and why: beakr sources tracked s1" in result.output


@respx.mock
def test_status_piped_counts_each_outcome():
    respx.get(f"{SOURCES}/s1/sync-status").respond(json=STATUS)
    respx.get(COMPILES).respond(json={"queued": 0, "running": 0, "last_cancel": None})
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    fields = result.stdout.splitlines()[0].split("\t")
    assert "items_skipped=1" in fields and "items_failed=1" in fields
    assert "items_ingested=2" in fields
