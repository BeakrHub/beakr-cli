"""Server text prints literally, never as Rich markup, wherever the CLI shows it.

Root cause: commands printed server-supplied text (page titles, breadcrumbs,
proposal fields, section and source titles, API error messages) through Rich
with markup on, often inside f-strings such as ``f"[bold]{title}[/bold]"``. Text
containing ``[...]`` was then read as markup: ``[hidden]`` or an unknown style
name silently dropped words, and a ``[link=...]`` or colour tag let whoever
wrote the title restyle or spoof what the person sees. Every such value is now
escaped (or printed with ``markup=False``); ``tests/test_table_cells_escaped.py``
covers table cells.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
PAGE_ID = "33333333-3333-3333-3333-333333333333"
PROJECT = "22222222-2222-2222-2222-222222222222"
PAGES = f"{API}/v1/knowledge/wiki/pages"
PROPOSALS = f"{API}/v1/knowledge/wiki/proposals"
runner = CliRunner()

# Each would lose words if Rich read it as markup: an unknown style tag is
# dropped, and a closing tag with no opening one is an error.
TITLE = "[hidden]Q3[/hidden] Roadmap"
BREADCRUMB = "Plans > [red]Launch[/red]"
SUMMARY = "[link=https://evil.test]Approve[/link] the [bold]buddy[/bold] system"
SOURCE = "[dim]Handbook[/dim].pdf"


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def terminal(monkeypatch):
    """Render for a person: CliRunner's stdout is not a TTY, which means compact lines."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _envelope(rows: list[dict]) -> dict:
    return {"data": rows, "next_cursor": None, "has_more": False}


@respx.mock
def test_search_results_print_titles_and_breadcrumbs_literally():
    respx.get(url__regex=rf"{API}/v1/knowledge/wiki/search.*").respond(
        json=_envelope([{"id": PAGE_ID, "title": TITLE, "breadcrumb": BREADCRUMB, "score": 0.9}])
    )

    result = runner.invoke(app, ["kb", "search", "roadmap"])

    assert result.exit_code == 0, result.output
    assert f"{TITLE}  score=0.90" in result.stdout
    assert BREADCRUMB in result.stdout


@respx.mock
def test_proposal_show_prints_its_fields_literally():
    respx.get(f"{PROPOSALS}/prop-1").respond(
        json={
            "id": "prop-1",
            "proposal_type": "edit",
            "status": "pending",
            "page_title": TITLE,
            "summary": SUMMARY,
            "payload": {},
        }
    )

    result = runner.invoke(app, ["kb", "proposals", "show", "prop-1"])

    assert result.exit_code == 0, result.output
    assert f"Page:   {TITLE}" in result.stdout
    assert f"Summary: {SUMMARY}" in result.stdout


@respx.mock
def test_proposal_accept_prints_the_returned_id_literally():
    respx.get(f"{PROPOSALS}/prop-1").respond(
        json={"id": "prop-1", "status": "pending", "content_hash": "b" * 64}
    )
    respx.post(f"{PROPOSALS}/prop-1/accept").respond(json={"proposal_id": "[red]prop-1[/red]"})

    result = runner.invoke(app, ["kb", "proposals", "accept", "prop-1", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Accepted proposal [red]prop-1[/red]." in result.stdout


@respx.mock
def test_page_headers_print_section_and_source_titles_literally():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json={"id": PAGE_ID, "title": "Plan"})
    respx.get(f"{PAGES}/{PAGE_ID}/section-provenance").respond(
        json={
            "provenance": {
                "s-1": {
                    "title": TITLE,
                    "sources": [{"stance": "support", "source_title": SOURCE, "key": "[k1]"}],
                }
            }
        }
    )

    result = runner.invoke(app, ["kb", "provenance", PAGE_ID])

    assert result.exit_code == 0, result.output
    assert TITLE in result.stdout
    assert f"support  {SOURCE} ([k1])" in result.stdout


@respx.mock
def test_page_log_table_title_prints_literally():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json={"id": PAGE_ID, "title": TITLE})
    respx.get(url__regex=rf"{PAGES}/{PAGE_ID}/revisions.*").respond(
        json=_envelope([{"revision": 1, "compiled_by": "agent", "summary": "first"}])
    )

    result = runner.invoke(app, ["kb", "log", PAGE_ID])

    assert result.exit_code == 0, result.output
    assert TITLE in result.stdout


@respx.mock
def test_api_error_messages_print_literally():
    respx.get(f"{PROPOSALS}/prop-1").respond(
        400,
        json={"error": {"code": "invalid", "message": f"Bad title {TITLE}: {SUMMARY}"}},
    )

    result = runner.invoke(app, ["kb", "proposals", "show", "prop-1"])

    assert result.exit_code == 1
    assert f"Error: Bad title {TITLE}: {SUMMARY}" in result.stderr
    # The JSON error object is unaffected: it never went through Rich.
    as_json = runner.invoke(app, ["kb", "proposals", "show", "prop-1", "--json"])
    assert TITLE in json.loads(as_json.stdout)["error"]["message"]
