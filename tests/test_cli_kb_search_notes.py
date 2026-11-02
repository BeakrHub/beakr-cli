"""`kb search` says when it found nothing, or nothing strong, in every output mode.

Root cause (Octant pilot, 2026-11-02): piped, `kb search` printed 0 bytes and
exited 0 for a query with no keyword match. ``machine_output`` handled the empty
list first, so "No results found." (terminal only) never ran, and an agent
concluded the knowledge base was empty while `kb sections` answered the same
query with weak matches.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
runner = CliRunner()
WEAK_NOTE = (
    "NOTE: every one of these results is a WEAK match -- none scored better than "
    "unrelated text would. Treat this as 'the corpus may not cover this' rather than "
    "as a ranked answer, and verify before relying on any of them."
)


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _answer(data: list[dict], **extra) -> None:
    respx.get(f"{API}/v1/knowledge/wiki/search").respond(
        json={"data": data, "next_cursor": None, "has_more": False, **extra}
    )


@respx.mock
def test_a_piped_empty_search_names_kb_sections(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: True)
    _answer([])

    result = runner.invoke(app, ["kb", "search", "titre drift"])

    assert result.exit_code == 0, result.output
    assert result.stdout == (
        "note: No strong matches. Weaker matches may exist: try "
        "`beakr kb sections 'titre drift'`.\n"
    )


@respx.mock
def test_an_empty_search_under_json_is_an_empty_list_with_the_hint_on_stderr():
    _answer([])

    result = runner.invoke(app, ["kb", "search", "titre", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert "Weaker matches may exist: try `beakr kb sections titre`." in result.stderr


@respx.mock
def test_an_empty_search_in_a_terminal_names_kb_sections(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    _answer([])

    result = runner.invoke(app, ["kb", "search", "titre"])

    assert result.exit_code == 0, result.output
    assert "No strong matches." in result.stderr
    assert "beakr kb sections titre" in result.stderr


@respx.mock
@pytest.mark.parametrize("as_terminal", [True, False], ids=["terminal", "piped"])
def test_a_weak_meaning_answer_carries_the_kb_sections_warning(monkeypatch, as_terminal):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: not as_terminal)
    _answer(
        [{"id": "p1", "title": "Helios", "score": 0.02, "relevance": "weak"}],
        match_strength="weak",
        note=WEAK_NOTE,
    )

    result = runner.invoke(app, ["kb", "search", "who owns the assay"])

    assert result.exit_code == 0, result.output
    if as_terminal:
        assert WEAK_NOTE in " ".join(result.stderr.split())
        assert "relevance=weak" in result.stdout
    else:
        assert result.stdout.splitlines()[-1] == f"note: {WEAK_NOTE}"
        assert result.stdout.splitlines()[0].startswith("p1\tHelios")


@respx.mock
def test_a_good_answer_carries_no_note(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: True)
    _answer([{"id": "p1", "title": "Helios", "score": 0.9}])

    result = runner.invoke(app, ["kb", "search", "Helios"])

    assert result.exit_code == 0, result.output
    assert "note:" not in result.stdout


def test_search_help_says_how_it_differs_from_sections():
    result = runner.invoke(app, ["kb", "search", "--help"])

    text = " ".join(result.stdout.split())
    assert "every word of the query" in text
    assert "beakr kb sections" in text
