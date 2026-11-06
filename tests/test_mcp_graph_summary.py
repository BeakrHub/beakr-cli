"""wiki_graph counts pages as pages, and wiki_stats says what each count counts.

ROOT CAUSE (Coding Agent Work, 2026-11-06): ``wiki_graph`` answered "Graph: 392
pages, 518 links" and "capped at 30 pages" while listing 15, beside
``wiki_stats``'s "Pages: 1430, Sources: 2789". The graph route returns the 30
pages it selected plus one node per source document they cite, with an edge
from each document to each citing page; both MCP servers counted every node as
a page and every edge as a link, listed the top 15, and called the result
capped whenever more than 30 nodes came back. ``wiki_stats``'s Sources is the
count of page-to-source citations, not of documents. The stdio texts are the
hosted ones (``formatting.graph_result`` and ``formatting.stats``), checked
against the engine's own functions where an engine checkout is available.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli import mcp_graph
from beakr_cli.mcp_server import mcp
from tests._engine import real_engine

API = "https://api.example.com"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


def _graph(page_count: int, source_count: int) -> dict[str, Any]:
    pages = [
        {"id": f"p{i}", "type": "page", "label": f"Page {i}", "page_type": "card"}
        for i in range(page_count)
    ]
    sources = [
        {"id": f"s{i}", "type": "source", "label": f"doc{i}.pdf"} for i in range(source_count)
    ]
    links = [{"from": f"p{i}", "to": "p0", "type": "links"} for i in range(1, page_count)]
    cites = [
        {"from": f"s{i}", "to": f"p{i % page_count}", "type": "source"} for i in range(source_count)
    ]
    return {"nodes": pages + sources, "edges": links + cites}


async def _call(name: str, graph: dict[str, Any], stats: dict[str, Any]) -> str:
    with respx.mock:
        respx.get(f"{API}/v1/knowledge/wiki/graph").mock(return_value=Response(200, json=graph))
        respx.get(f"{API}/v1/knowledge/stats").mock(return_value=Response(200, json=stats))
        handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(
            method="tools/call", params=types.CallToolRequestParams(name=name, arguments={})
        )
        result = (await handler(request)).root
    assert not result.isError, result.content
    return result.content[0].text


def test_the_graph_summary_counts_pages_not_source_documents() -> None:
    result = mcp_graph.graph_result(_graph(30, 362), total_pages=1430)
    text = result["content"]
    assert text.startswith(
        "Graph: the 30 most linked-to pages of 1430, 29 links among them, "
        "citing 362 source documents"
    )
    # Every page returned is listed, and no source document is listed as a page.
    assert all(f"Page {i} (card)" in text for i in range(30))
    assert "doc0.pdf" not in text
    assert result["coverage"]["returned"] == 30 and result["coverage"]["total"] == 1430
    assert result["coverage"]["complete"] is False
    assert "Showing the 30 most linked-to pages of 1430" in result["next"][0]


def test_a_graph_showing_every_page_is_complete() -> None:
    result = mcp_graph.graph_result(_graph(4, 50), total_pages=4)
    assert result["coverage"]["complete"] is True and result["next"] == []
    assert "Graph: the 4 most linked-to pages of 4, 3 links among them" in result["content"]


def test_stats_say_what_each_count_counts() -> None:
    assert mcp_graph.stats({"pages": 1430, "sources": 2789, "links": 9000}) == (
        "Pages: 1430 (live pages in scope)\n"
        "Sources: 2789 (page-to-source citations: a document cited by three pages "
        "counts three times)"
    )


@pytest.mark.asyncio
async def test_the_stdio_tools_read_the_graph_and_the_scopes_page_count() -> None:
    stats = {"pages": 1430, "sources": 2789, "links": 9000}
    graph_text = await _call("wiki_graph", _graph(30, 362), stats)
    assert graph_text.startswith("Graph: the 30 most linked-to pages of 1430, 29 links")
    assert "[Showing the 30 most linked-to pages of 1430" in graph_text
    assert await _call("wiki_stats", {}, stats) == mcp_graph.stats(stats)


_HOSTED = """\
import json, sys
from beakr.api.mcp_remote import formatting
if not hasattr(formatting, "stats"):
    print(json.dumps(None))
    raise SystemExit
cases = json.load(sys.stdin)
print(json.dumps({
    "graph": [formatting.graph_result(c["graph"], total_pages=c["total"]) for c in cases],
    "stats": formatting.stats({"pages": 1430, "sources": 2789}),
}))
"""


def test_the_stdio_texts_are_the_hosted_ones_byte_for_byte() -> None:
    engine = real_engine("beakr/api/mcp_remote/formatting.py")
    cases = [
        {"graph": _graph(30, 362), "total": 1430},
        {"graph": _graph(4, 50), "total": 4},
        {"graph": _graph(12, 0), "total": None},
        {"graph": {}, "total": None},
    ]
    hosted = subprocess.run(
        [str(engine / ".venv/bin/python"), "-c", _HOSTED],
        cwd=engine,
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        check=True,
    )
    expected = json.loads(hosted.stdout)
    if expected is None:
        pytest.skip("this engine checkout predates the hosted graph summary fix")
    stdio = [mcp_graph.graph_result(c["graph"], total_pages=c["total"]) for c in cases]
    assert json.loads(json.dumps(stdio)) == expected["graph"]
    assert mcp_graph.stats({"pages": 1430, "sources": 2789}) == expected["stats"]
