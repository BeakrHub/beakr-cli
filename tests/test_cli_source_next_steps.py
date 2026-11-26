"""The commands ``beakr sources`` suggests run as printed.

r108 sweep (sandbox, 26 Nov 2026): an agent runs a ``next:`` line as it is,
often outside a directory bound to the project. The per-source suggestions
dropped ``-P``, so each failed with "Pass --project". The next page of
``sources tracked`` also dropped the filters: the cursor carries only the
position, so with ``-P`` added the next page listed every item of the source
instead of the next filtered ones. And an attach with Knowledge off suggested
``sources sync``, which the engine refuses for such a source ("Wiki ingestion is
not enabled for this source", 400).
"""

from __future__ import annotations

import shlex

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "11111111-2222-4333-8444-555555555555"
SOURCES = f"{API}/v1/projects/{PROJECT}/sources"
TRACKED = f"{SOURCES}/s1/tracked-items"
NOTION_ID = "0123456789abcdef0123456789abcdef"
runner = CliRunner()

ITEM = {"external_id": "conn:t1", "title": "Archive table 7", "kind": "item", "state": "ingested"}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _page(cursor: str | None) -> dict:
    return {
        "data": [ITEM],
        "next_cursor": cursor,
        "has_more": cursor is not None,
        "provider": "notion",
        "kind": "item",
        "counts": {"total": 68, "ingested": 34, "skipped": 34},
        "matched": 10,
    }


def _next_line(stdout: str) -> list[str]:
    hint = next(line for line in stdout.splitlines() if line.startswith("next: "))
    return shlex.split(hint.removeprefix("next: "))


FILTERS = [
    "--state",
    "ingested",
    "--reason-code",
    "nothing_to_add",
    "--search",
    "Archive table",
    "--since",
    "2026-11-25T00:00:00Z",
]


@respx.mock
def test_the_next_page_of_tracked_keeps_the_project_and_every_filter():
    route = respx.get(TRACKED).respond(json=_page("c2"))
    result = runner.invoke(
        app, ["sources", "tracked", "s1", "-P", PROJECT, "--limit", "5", *FILTERS]
    )
    assert result.exit_code == 0, result.output
    assert _next_line(result.stdout) == [
        "beakr",
        "sources",
        "tracked",
        "s1",
        *FILTERS,
        "--limit",
        "5",
        "--cursor",
        "c2",
        "-P",
        PROJECT,
    ]

    # Run as printed, the next page asks the route with the same filters.
    route.respond(json=_page(None))
    again = runner.invoke(app, _next_line(result.stdout)[1:])
    assert again.exit_code == 0, again.output
    sent = route.calls.last.request.url.params
    assert (sent["state"], sent["search"], sent["reason_code"]) == (
        "ingested",
        "Archive table",
        "nothing_to_add",
    )
    assert (sent["cursor"], sent["limit"]) == ("c2", "5")
    assert not any(line.startswith("next: ") for line in again.stdout.splitlines())


@respx.mock
def test_skipped_pages_on_as_state_skipped_and_the_default_limit_is_left_out():
    respx.get(TRACKED).respond(json=_page("c2"))
    result = runner.invoke(app, ["sources", "tracked", "s1", "--skipped", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert _next_line(result.stdout)[4:] == ["--state", "skipped", "--cursor", "c2", "-P", PROJECT]


@respx.mock
def test_the_terminal_names_the_same_next_page(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    respx.get(TRACKED).respond(json=_page("c2"))
    result = runner.invoke(app, ["sources", "tracked", "s1", "--state", "ingested", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert (
        f"More: beakr sources tracked s1 --state ingested --cursor c2 -P {PROJECT} (or --all)"
        in result.stderr
    )


def _attached(**fields) -> dict:
    return {
        "id": "s1",
        "target_type": "project",
        "target_id": PROJECT,
        "provider": "notion:nango",
        "connector_id": "c1",
        "external_item_id": "01234567-89ab-cdef-0123-456789abcdef",
        "external_item_type": "database",
        "external_item_name": "Reagents",
        "ingest_wiki": False,
        "auto_sync_wiki": False,
        "scope_mode": "item",
        **fields,
    }


def _attach(*extra: str) -> list[str]:
    return [
        "sources",
        "attach",
        f"https://www.notion.so/{NOTION_ID}",
        "--connector",
        "c1",
        "--type",
        "database",
        *extra,
        "-P",
        PROJECT,
    ]


@respx.mock
def test_attaching_with_knowledge_off_points_at_turning_knowledge_on(monkeypatch):
    respx.post(f"{API}/v1/integrations/tools/notion_metadata/call").respond(404, json={})
    respx.post(SOURCES).respond(201, json=_attached())
    result = runner.invoke(app, _attach())
    assert result.exit_code == 0, result.output
    assert _next_line(result.stdout) == [
        "beakr",
        "sources",
        "edit",
        "s1",
        "--knowledge",
        "-P",
        PROJECT,
    ]

    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    shown = runner.invoke(app, _attach())
    assert (
        f"Nothing is read until Knowledge is on: beakr sources edit s1 --knowledge -P {PROJECT}"
        in shown.stderr
    )
    assert "sources sync" not in shown.output


@respx.mock
def test_attaching_with_knowledge_on_points_at_its_sync_in_the_project():
    respx.post(f"{API}/v1/integrations/tools/notion_metadata/call").respond(404, json={})
    respx.post(SOURCES).respond(201, json=_attached(ingest_wiki=True))
    result = runner.invoke(app, _attach("--knowledge"))
    assert result.exit_code == 0, result.output
    assert _next_line(result.stdout) == ["beakr", "sources", "sync", "s1", "-P", PROJECT]


@respx.mock
def test_a_sync_and_its_status_name_the_project():
    respx.post(f"{SOURCES}/s1/sync").respond(
        202, json={"status": "syncing", "sync_job_id": "j1", "attachment_id": "s1"}
    )
    result = runner.invoke(app, ["sources", "sync", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert _next_line(result.stdout) == ["beakr", "sources", "status", "s1", "-P", PROJECT]
