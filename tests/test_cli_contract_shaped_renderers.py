"""Renderers read the fields the public contract documents, not the ones they guessed.

Each test answers with the operation's documented response shape
(docs/api/public-openapi.json in beakr-engine) and checks what a person sees.
The command snapshots (tests/test_command_snapshots.py) found every one of these
against recorded responses; these pin the behavior one command at a time.
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
PAGE = {"id": PAGE_ID, "title": "Onboarding", "project_id": PROJECT, "revision": 3}
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def terminal(monkeypatch):
    """Render for a person: CliRunner's stdout is not a TTY, which means JSON."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _flat(text: str) -> str:
    return " ".join(text.split())


# --- kb links / kb sources ------------------------------------------------------
# Root cause: both read `data.get("backlinks")` / `data.get("sources")`, but the SDK
# follows the paginated WikiBacklinks / WikiPageSources envelope and returns its
# rows as a list, so every answer raised AttributeError (--json included).


@respx.mock
def test_kb_links_lists_the_backlink_rows(terminal):
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    respx.get(f"{PAGES}/{PAGE_ID}/backlinks").respond(
        json={
            "data": [{"id": "p-1", "title": "Hiring Plan", "summary": "Who we hire."}],
            "has_more": False,
            "next_cursor": None,
        }
    )
    result = runner.invoke(app, ["kb", "links", PAGE_ID])
    assert result.exit_code == 0, result.output
    assert "Hiring Plan" in result.stdout

    as_json = runner.invoke(app, ["kb", "links", PAGE_ID, "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.stdout) == [
        {"id": "p-1", "title": "Hiring Plan", "summary": "Who we hire."}
    ]


@respx.mock
def test_kb_sources_lists_the_source_rows(terminal):
    row = {
        "source_type": "artifact",
        "source_title": "handbook.pdf",
        "source_id": "a-1",
        "key": "artifact:a-1",
        "stance": "support",
    }
    respx.get(f"{PAGES}/{PAGE_ID}").respond(json=PAGE)
    respx.get(f"{PAGES}/{PAGE_ID}/sources").respond(
        json={"data": [row], "has_more": False, "next_cursor": None}
    )
    result = runner.invoke(app, ["kb", "sources", PAGE_ID])
    assert result.exit_code == 0, result.output
    assert "handbook.pdf" in result.stdout and "support" in result.stdout

    # --json prints each row as the public source record the MCP server returns.
    as_json = runner.invoke(app, ["kb", "sources", PAGE_ID, "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.stdout) == [
        {
            "citation_key": "artifact:a-1",
            "name": "handbook.pdf",
            "filename": "handbook.pdf",
            "source_kind": "artifact",
            "stance": "support",
        }
    ]


# --- kb graph -------------------------------------------------------------------
# Root cause: it printed node `title`, but a WikiGraphNode carries the page title
# in `label`, so every node printed as its id; and the "[type]" prefix went
# through Rich markup, which swallowed it as an unknown style tag.


@respx.mock
def test_kb_graph_prints_each_node_by_its_label_and_page_type(terminal):
    respx.get(f"{API}/v1/knowledge/wiki/graph").respond(
        json={
            "nodes": [{"id": "n-1", "label": "Onboarding", "page_type": "process", "type": "page"}],
            "edges": [],
        }
    )
    result = runner.invoke(app, ["kb", "graph"])
    assert result.exit_code == 0, result.output
    assert "[process] Onboarding" in result.stdout
    assert "n-1" not in result.stdout


# --- kb research ----------------------------------------------------------------
# Root cause: it read `citations`, but a ResearchResult carries `sources`, an
# object keyed by the citation key the answer's {{key}} tokens use, so it printed
# the raw tokens and no sources.

_RESEARCH = {
    "answer": "Pairs for a week {{artifact:a-1}}, then ships {{wiki:p-9}}; see {{artifact:a-1}}.",
    "sources": {
        "artifact:a-1": {
            "key": "artifact:a-1",
            "type": "artifact",
            "title": "handbook.pdf",
            "meta": {"web_view_url": "https://drive.example/handbook"},
        },
        "wiki:p-9": {"key": "wiki:p-9", "type": "wiki", "title": "Shipping"},
    },
    "confidence": "high",
    "gaps": None,
}


@pytest.mark.parametrize("argv", [["kb", "research"], ["research"]])
@respx.mock
def test_research_numbers_citations_and_lists_their_sources(terminal, argv):
    respx.post(f"{API}/v1/knowledge/research").respond(json=_RESEARCH)
    result = runner.invoke(app, [*argv, "How does onboarding work?"])
    assert result.exit_code == 0, result.output
    out = _flat(result.stdout)
    assert "{{" not in result.stdout
    assert "Pairs for a week [1], then ships [2]; see [1]." in out
    # Each source says where it lives (filename, then the link to the original).
    assert "[1] handbook.pdf · https://drive.example/handbook" in out
    assert "[2] Shipping" in out
    assert "Confidence: high" in out


@respx.mock
def test_research_cites_a_key_missing_from_sources_by_its_key(terminal):
    respx.post(f"{API}/v1/knowledge/research").respond(
        json={"answer": "Known {{artifact:zz}}.", "sources": {}}
    )
    result = runner.invoke(app, ["kb", "research", "q"])
    assert result.exit_code == 0, result.output
    assert "[1] artifact:zz" in _flat(result.stdout)


# --- conversations: compound state `none` ----------------------------------------
# Root cause: `none` is the engine's state for a conversation never compounded
# (imported_conversations.compound_status), but the CLI only knew `pending`, so
# `none` printed as the raw word.

_CONVERSATION = {
    "id": "c-1",
    "kind": "imported",
    "client": "claude-code",
    "session_id": "s-1",
    "title": "Wire the import",
    "message_count": 4,
    "compounded_through": 0,
    "compound_status": "none",
    "last_compound_result": None,
    "created_at": "2026-10-01T12:00:00Z",
    "updated_at": "2026-10-01T12:00:00Z",
}


@respx.mock
def test_conversation_never_compounded_reads_as_not_compounded_yet(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    respx.get(f"{API}/v1/conversations/c-1").respond(json=_CONVERSATION)
    shown = runner.invoke(app, ["conversations", "show", "c-1"])
    assert shown.exit_code == 0, shown.output
    assert "Compounded: 0/4 messages, not compounded yet" in shown.stdout
    assert ", none" not in shown.stdout

    respx.get(f"{API}/v1/conversations").respond(
        json={"data": [_CONVERSATION], "has_more": False, "next_cursor": None}
    )
    listed = runner.invoke(app, ["conversations", "ls"])
    assert listed.exit_code == 0, listed.output
    assert "not yet 0/4" in listed.stdout
    assert "none" not in listed.stdout
