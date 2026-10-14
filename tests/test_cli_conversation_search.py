"""``beakr conversations search`` and ``freshness`` (engine W4.4)."""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def terminal(monkeypatch):
    # CliRunner is not a TTY, so emit() would print JSON; render as a person sees it.
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


@respx.mock
def test_search_prints_hits_and_passes_options(terminal):
    route = respx.get(f"{API}/v1/conversations/search").respond(
        json={
            "data": [
                {
                    "kind": "imported",
                    "id": "c1",
                    "title": "Pump calibration",
                    "summary": "Chose the March curve",
                    "updated_at": "2026-10-01T00:00:00",
                    "findings": [{"page_title": "Pump calibration"}],
                }
            ]
        }
    )
    result = CliRunner().invoke(
        app, ["conversations", "search", "which pump curve", "--any", "--no-chats"]
    )
    assert result.exit_code == 0, result.output
    assert "Pump calibration" in result.output and "imported session" in result.output
    assert "findings: 1" in result.output
    params = route.calls.last.request.url.params
    assert params["match"] == "any" and params["include_threads"] == "false"


@respx.mock
def test_search_json():
    respx.get(f"{API}/v1/conversations/search").respond(json={"data": [{"id": "c1"}]})
    result = CliRunner().invoke(app, ["conversations", "search", "pump", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == [{"id": "c1"}]


@respx.mock
def test_freshness_lists_changed_pages(terminal):
    respx.get(f"{API}/v1/conversations/c1/freshness").respond(
        json={
            "indexed": True,
            "fresh": False,
            "pages_checked": 1,
            "changed": [
                {
                    "page_id": "p",
                    "title": "Pump calibration",
                    "read_revision": 1,
                    "current_revision": 2,
                    "state": "changed",
                }
            ],
            "unchecked_files": ["notes.md"],
        }
    )
    result = CliRunner().invoke(app, ["conversations", "freshness", "c1"])
    assert result.exit_code == 0, result.output
    assert "Pump calibration (changed, read revision 1, now 2)" in result.output
    assert "notes.md" in result.output


@respx.mock
def test_freshness_of_an_unindexed_conversation(terminal):
    respx.get(f"{API}/v1/conversations/c1/freshness").respond(
        json={"indexed": False, "fresh": False, "changed": [], "unchecked_files": []}
    )
    result = CliRunner().invoke(app, ["conversations", "freshness", "c1"])
    assert "Not indexed yet" in result.output


@respx.mock
def test_freshness_says_unknown_when_nothing_could_be_checked(terminal):
    # Audit C031: a session whose reads have no revisions is never reported fresh.
    respx.get(f"{API}/v1/conversations/c1/freshness").respond(
        json={
            "conversation_id": "c1",
            "indexed": True,
            "fresh": None,
            "coverage": "none",
            "pages_checked": 0,
            "changed": [],
            "unchecked_pages": [],
            "unchecked_sources": [],
            "unchecked_files": ["data.csv"],
        }
    )
    result = CliRunner().invoke(app, ["conversations", "freshness", "c1"])
    assert result.exit_code == 0, result.output
    assert "Unknown" in result.output and "Fresh" not in result.output
    assert "data.csv" in result.output


@respx.mock
def test_freshness_partial_coverage_lists_what_was_not_checked(terminal):
    respx.get(f"{API}/v1/conversations/c1/freshness").respond(
        json={
            "conversation_id": "c1",
            "indexed": True,
            "fresh": None,
            "coverage": "partial",
            "pages_checked": 2,
            "changed": [],
            "unchecked_pages": [{"page_id": "p9", "title": "Assay notes"}],
            "unchecked_sources": [{"source_ref": "src_a", "version": None}],
            "unchecked_files": [],
        }
    )
    result = CliRunner().invoke(app, ["conversations", "freshness", "c1"])
    assert result.exit_code == 0, result.output
    assert "partial coverage" in result.output
    assert "Assay notes" in result.output and "src_a" in result.output
