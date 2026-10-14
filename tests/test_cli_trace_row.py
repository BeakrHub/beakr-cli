"""``beakr trace row <card> --row ...``: one Knowledge value's trace (mocked engine routes)."""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
CARD = "11111111-2222-3333-4444-555555555555"
runner = CliRunner()

_TRACE = {
    "kind": "row",
    "id": CARD,
    "title": "Helios HX-07",
    "table": "facts",
    "value": "41 C",
    "row": {"index": 3, "cells": {"attribute": "Temperature", "value": "41 C", "when": "2026-05"}},
    "inputs": [
        {
            "citation_key": "doc1",
            "source_ref": "beakr-source:v1:abc",
            "name": "hx01_report.pdf",
            "version": {"unit_version_id": "uv-1", "current_unit_version_id": "uv-2"},
        }
    ],
    "introduced_by": {
        "revision": 2,
        "change": "added",
        "cells": {"attribute": "Temperature", "value": "37 C"},
        "proposal": {"id": "prop-1", "proposed_by": "alice", "reviewed_by": "alice"},
        "via": {"channel": "api", "credential_kind": "apikey", "client": "cli", "agent": "bot"},
        "run": {"type": "label", "id": "session-7"},
    },
    "last_changed_by": {
        "revision": 3,
        "change": "changed",
        "cells": {"attribute": "Temperature", "value": "41 C"},
        "proposal": {"id": "prop-2", "proposed_by": "alice"},
        "via": None,
        "run": None,
    },
    "history_complete": True,
}
_TRACE["history"] = [_TRACE["last_changed_by"], _TRACE["introduced_by"]]


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def rendered(monkeypatch):
    """Render tables, as in a wide terminal, instead of JSON for a pipe."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    monkeypatch.setenv("COLUMNS", "250")


@respx.mock
def test_trace_row_by_card_id_sends_the_table_row_and_value():
    route = respx.get(f"{API}/v1/trace/row/{CARD}").respond(200, json=_TRACE)
    result = runner.invoke(
        app,
        ["trace", "row", CARD, "--table", "facts", "--row", "Temperature", "--value", "41", "-j"],
    )
    assert result.exit_code == 0, result.output
    params = route.calls.last.request.url.params
    assert params["table"] == "facts" and params["row"] == "Temperature"
    assert params["value"] == "41"
    assert json.loads(result.output)["value"] == "41 C"


@respx.mock
def test_trace_row_resolves_a_card_title_and_renders_sources_and_history(rendered):
    respx.get(f"{API}/v1/knowledge/wiki/pages/by-title").respond(
        200, json={"id": CARD, "title": "Helios HX-07"}
    )
    route = respx.get(f"{API}/v1/trace/row/{CARD}").respond(200, json=_TRACE)
    result = runner.invoke(app, ["trace", "row", "Helios HX-07", "--row", "3"])
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["row"] == "3"
    assert "Temperature" in result.output
    assert "proposal prop-1" in result.output and "agent=bot" in result.output
    assert "run label:session-7" in result.output
    assert "beakr-source:v1:abc" in result.output
    assert "newer version exists" in result.output
    assert "37 C" in result.output and "41 C" in result.output


def test_trace_row_needs_a_row_and_a_known_table():
    assert runner.invoke(app, ["trace", "row", CARD]).exit_code == 2
    assert runner.invoke(app, ["trace", "row", CARD, "--row", "1", "--table", "x"]).exit_code == 2
    assert runner.invoke(app, ["trace", "row"]).exit_code == 2


@respx.mock
def test_trace_row_of_an_unknown_row_maps_to_not_found():
    respx.get(f"{API}/v1/trace/row/{CARD}").respond(404, json={"detail": "No facts row"})
    assert runner.invoke(app, ["trace", "row", CARD, "--row", "Viscosity"]).exit_code == 4
