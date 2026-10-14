"""``beakr kb ls`` with no project says why the list is empty.

Root cause of the regression this guards: with no project the API lists only
organization-root pages, usually none, and the CLI printed ``[]`` with no hint
while the projects held pages.
"""

from __future__ import annotations

import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"


_EMPTY_PAGE = {"data": [], "next_cursor": None, "has_more": False}


@respx.mock
def test_empty_unscoped_listing_explains_how_to_scope(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    respx.get(f"{API}/v1/knowledge/wiki/pages").respond(json=_EMPTY_PAGE)
    result = CliRunner().invoke(app, ["kb", "ls", "--json"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "[]"
    assert "No project is selected" in result.stderr


@respx.mock
def test_a_scoped_empty_listing_has_no_hint(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    respx.get(f"{API}/v1/knowledge/wiki/pages").respond(json=_EMPTY_PAGE)
    result = CliRunner().invoke(app, ["kb", "ls", "--project", "p1", "--json"])
    assert result.exit_code == 0, result.output
    assert "No project is selected" not in result.stderr
