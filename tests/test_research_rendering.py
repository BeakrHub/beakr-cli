"""Research answers in the engine's public form render as the hosted MCP server renders them.

The engine's ``POST /v1/knowledge/research`` now answers with ``text`` (each
cited document numbered once as ``[n]``), ``citations`` (one record per
document, brief excerpts) and, when it trimmed excerpts, ``next``. Root cause
this guards: the stdio server and ``beakr research`` read only the older
``answer`` / ``sources`` form, so against a current engine they printed no
Sources at all, and a research run that cited 210 records (proof run
2026-10-13) would have listed every one. Both now render ``text`` and
``citations``, the Sources list held to the hosted ``SOURCES_CHARS`` with a
"+N more sources" line; the stdio result is byte for byte the hosted
``formatting.research`` (checked against the engine's own code where a real
engine checkout is available, as in the engine's CI).
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import mcp_render, mcp_result, mcp_server
from beakr_cli.cited_answer import research_lines
from beakr_cli.main import app
from tests._engine import real_engine

API = "https://api.test"


def _many_citations() -> dict:
    return {
        "text": "Answer [1] and [200].",
        "citations": [
            {
                "citation_key": f"artifact:v{n}-u1",
                "filename": f"Report {n}.pdf",
                "location": {"file_id": f"f{n}", "display_path": f"Reports/Report {n}.pdf"},
                "evidence": {"excerpt": "The assay showed a dose-response. " * 20},
            }
            for n in range(200)
        ],
        "answer": "",
        "sources": {},
    }


def _two_documents() -> dict:
    return {
        "text": "The lead is NW-217 [1], confirmed in the March review [2].",
        "citations": [
            {
                "citation_key": "artifact:v1-u3",
                "filename": "Lead selection.pdf",
                "location": {"file_id": "f1", "display_path": "Leads/Lead selection.pdf"},
                "evidence": {"excerpt": "NW-217 was selected as the lead."},
                "cited_passages": ["artifact:v1-u3", "artifact:v1-u7"],
                "excerpts": [
                    {"citation_key": "artifact:v1-u3", "text": "NW-217 was selected."},
                    {"citation_key": "artifact:v1-u7", "text": "Thermostable at 60C."},
                ],
            },
            {
                "citation_key": "page:review-u1",
                "title": "March review",
                "source_kind": "knowledge_page",
                "evidence": {"excerpt": "Review confirmed NW-217."},
            },
        ],
        "sources": [{"key": "artifact:v1-u3", "source_ref": "sr1"}],
        "next": ["knowledge_base command='sources' with a citation token reads the full excerpt."],
        "confidence": "high",
        "gaps": "No data after March.",
    }


def _stdio(data: dict) -> dict:
    return mcp_result.finish(
        mcp_server._research_from_citations(data, data["citations"]), tool="research"
    )


def test_sources_are_numbered_once_per_document_and_capped() -> None:
    text = _stdio(_many_citations())["content"]
    assert text.startswith("Answer [1] and [200].")
    assert "[1] Reports/Report 0.pdf: {{artifact:v0-u1}}" in text
    listing = text.split("Sources:", 1)[1]
    assert len(listing) < mcp_render.SOURCES_CHARS + 2_000
    assert re.search(r"\+\d+ more sources: knowledge_base command='sources'", listing)


def test_next_confidence_and_gaps_are_kept() -> None:
    body = _stdio(_two_documents())
    assert body["next"] == _two_documents()["next"]
    assert body["confidence"] == "high"
    assert "Confidence: high | Gaps: No data after March." in body["content"]
    assert [s.get("citation_key") for s in body["sources"]] == ["artifact:v1-u3", "page:review-u1"]


@pytest.mark.asyncio
@respx.mock
async def test_the_stdio_tool_takes_the_public_form(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    respx.post(f"{API}/v1/knowledge/research").respond(200, json=_two_documents())
    body = await mcp_server.research(query="Which lead?")
    assert mcp_result.finish(body, tool="research") == _stdio(_two_documents())


@pytest.mark.parametrize("data", [_many_citations(), _two_documents()], ids=["many", "two"])
def test_the_stdio_result_is_the_hosted_one_byte_for_byte(data) -> None:
    engine = real_engine("beakr/api/mcp_remote/formatting.py")
    hosted = subprocess.run(
        [
            str(engine / ".venv/bin/python"),
            "-c",
            "import json, sys\n"
            "from beakr.api.mcp_remote import formatting\n"
            "print(json.dumps(formatting.research(json.load(sys.stdin))))",
        ],
        cwd=engine,
        input=json.dumps(data),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(json.dumps(_stdio(data))) == json.loads(hosted.stdout)


def test_research_lines_number_the_documents_and_cap_the_list() -> None:
    lines = research_lines(_two_documents())
    assert lines[0] == "The lead is NW-217 [1], confirmed in the March review [2]."
    sources = lines[lines.index("sources:") + 1 :]
    assert sources[0].startswith("[1]\tLead selection.pdf")
    assert sources[1].startswith("[2]\t")
    assert "next:" in lines
    many = research_lines(_many_citations())
    listed = many[many.index("sources:") + 1 :]
    assert sum(len(line) + 1 for line in listed[:-1]) <= mcp_render.SOURCES_CHARS
    assert re.fullmatch(r"\+\d+ more sources \(--json lists them all\)", listed[-1])


@respx.mock
def test_beakr_research_prints_text_sources_and_next(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    respx.post(f"{API}/v1/knowledge/research").respond(200, json=_two_documents())
    result = CliRunner().invoke(app, ["research", "Which lead?"])
    assert result.exit_code == 0, result.output
    assert "NW-217 [1]" in result.output
    assert "[1] Lead selection.pdf" in result.output
    assert "reads the full excerpt" in result.output


def test_an_older_engines_answer_still_renders() -> None:
    lines = research_lines(
        {"answer": "Five minutes {{a:1}}.", "sources": {"a:1": {"title": "Lease"}}}
    )
    assert lines[0] == "Five minutes [1]."
    assert lines[lines.index("sources:") + 1].startswith("[1]\t")
