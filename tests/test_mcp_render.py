"""Each stdio MCP result goes out once, as text the model reads.

ROOT CAUSE (Harvey LAB traces, scratchpad/dogfood/excerpt-tuning.md): every
Knowledge result was sent twice -- the dict as a JSON text block and again as
``structuredContent`` -- so Codex printed both into an exec cell it cut at
~10k tokens, and Claude Code refused 41% of results over its ~50k-character
limit. Now no tool declares an ``outputSchema`` and a result is one text block:
the content, a compact Sources list, and the ``next`` hints.
"""

from __future__ import annotations

import json
from pathlib import Path

from beakr_cli import mcp_server
from beakr_cli.mcp_render import EXCERPT_CHARS, SOURCES_CHARS, render

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "knowledge_door_results.json").read_text()
)


async def test_no_tool_declares_an_output_schema():
    # A declared outputSchema obliges every result to carry structuredContent.
    for tool in await mcp_server.mcp.list_tools():
        assert tool.outputSchema is None, tool.name


def test_a_cat_result_is_smaller_than_the_one_json_copy_it_replaces():
    shaped = _CASES["reads"]["cat"]["mcp"]
    text = render("knowledge_base", shaped)
    assert shaped["content"] in text
    # It used to go out as this JSON twice; the rendering is under one copy.
    assert len(text) < len(json.dumps(shaped, indent=2))


def test_sources_list_names_each_document_once_with_its_file_and_one_excerpt():
    passage = {
        "citation_key": "artifact:v1-u1",
        "name": "charter.pdf",
        "filename": "charter.pdf",
        "provider": "upload",
        "location": {"file_id": "f-1"},
        "evidence": {"excerpt": "x" * 1000},
    }
    second = {**passage, "citation_key": "artifact:v1-u2", "evidence": {"excerpt": "later"}}
    text = render(
        "knowledge_base", {"content": "Body {{artifact:v1-u1}}", "sources": [passage, second]}
    )
    sources = text.split("Sources (1 document):", 1)[1]
    # The path leads; ``upload`` is left out (the read_file id says Beakr holds it).
    assert "- charter.pdf: {{artifact:v1-u1}} {{artifact:v1-u2}} · read_file f-1" in sources
    excerpt = sources.split('"', 2)[1]
    assert len(excerpt) <= EXCERPT_CHARS and "later" not in sources


def test_a_long_source_list_is_capped_and_says_where_the_rest_is():
    records = [
        {
            "citation_key": f"artifact:d{n}-u1",
            "filename": f"doc{n}.pdf",
            "evidence": {"excerpt": "y" * 400},
        }
        for n in range(200)
    ]
    block = render("knowledge_base", {"content": "Body", "sources": records}).split("\n\n", 1)[1]
    assert len(block) < SOURCES_CHARS + 300
    assert "more sources: knowledge_base command='sources'" in block


def test_research_keeps_its_own_numbered_sources_and_gets_no_second_list():
    body = {
        "content": "Five minutes [1].\n\nSources:\n  [1] doc",
        "sources": [{"citation_key": "rag:1"}],
    }
    assert render("research", body) == body["content"]


def test_hints_are_added_once():
    body = {
        "content": "Page 1\n\n[More results: call x again]",
        "next": ["More results: call x again"],
    }
    assert render("list_projects", body) == body["content"]
    assert render("cat", {"content": "Cut", "next": ["Read a section."]}).endswith(
        "next: Read a section."
    )


def test_each_token_is_followed_by_where_its_passage_sits():
    record = {
        "citation_key": "artifact:v1-u1",
        "filename": "book.xlsx",
        "provider": "google_drive",
        "location": {
            "display_path": "Finance/2026/book.xlsx",
            "unit_locator": "sheet 2, B4:D9",
            "url": "https://drive.example/book",
        },
        "excerpts": [{"citation_key": "artifact:v1-u3", "unit_locator": "sheet 3", "text": "Q3"}],
    }
    text = render("knowledge_base", {"content": "x", "sources": [record]})
    assert (
        "- Finance/2026/book.xlsx: {{artifact:v1-u1}} › sheet 2, B4:D9 {{artifact:v1-u3}} "
        "› sheet 3 · google_drive · https://drive.example/book"
    ) in text
