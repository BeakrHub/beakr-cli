"""The ``wiki_graph`` and ``wiki_stats`` texts, as the hosted MCP server renders them.

A copy of ``graph``, ``graph_result`` and ``stats`` in the engine's
``beakr/api/mcp_remote/formatting.py``: the two repositories share no package,
and ``tests/test_mcp_graph_summary.py`` checks this copy against the engine's
own functions where an engine checkout is available.

ROOT CAUSE (Coding Agent Work, 2026-11-06): the graph route returns the pages
it selected (at most :data:`GRAPH_MAX_NODES`) AND one node per source document
those pages cite, with an edge from each document to each page citing it. Both
servers counted every node as a page and every edge as a link ("392 pages, 518
links" for a request capped at 30 pages), listed only 15 of them, and called the
summary capped whenever more than 30 nodes came back. Pages are now counted as
pages, links only among them, every page shown is listed, and the result is
complete only when the scope's every page is shown.
"""

from __future__ import annotations

from typing import Any

from beakr_cli.mcp_result import capped_result

#: Pages the graph summary asks for.
GRAPH_MAX_NODES = 30


def _graph_parts(data: dict) -> tuple[list[dict], list[dict], int]:
    """The page nodes, the links among them, and how many source-document nodes there are."""
    nodes = [node for node in data.get("nodes", []) if isinstance(node, dict)]
    pages = [node for node in nodes if node.get("type") != "source"]
    page_ids = {node.get("id") for node in pages}
    links = [
        edge
        for edge in data.get("edges", [])
        if isinstance(edge, dict)
        and edge.get("type") != "source"
        and edge.get("from") in page_ids
        and edge.get("to") in page_ids
    ]
    return pages, links, len(nodes) - len(pages)


def graph(data: dict, *, total_pages: int | None = None) -> str:
    """``Graph: the N most linked-to pages of T, L links among them, citing M source documents``."""
    pages, links, sources = _graph_parts(data)
    connections: dict[str, int] = {}
    for edge in links:
        for end in (edge.get("from"), edge.get("to")):
            connections[end] = connections.get(end, 0) + 1
    of_total = f" of {total_pages}" if total_pages is not None else ""
    head = f"Graph: the {len(pages)} most linked-to pages{of_total}, {len(links)} links among them"
    if sources:
        head += f", citing {sources} source documents"
    lines = [f"{head}\n", "Pages, by links among the pages shown:"]
    ranked = sorted(
        pages,
        key=lambda node: (-connections.get(node.get("id"), 0), str(node.get("label") or "")),
    )
    for node in ranked:
        label = node.get("label") or str(node.get("id"))[:8]
        count = connections.get(node.get("id"), 0)
        lines.append(f"  {label} ({node.get('page_type') or ''}) - {count} links")
    return "\n".join(lines)


def graph_result(data: dict, *, total_pages: int | None = None) -> dict[str, Any]:
    """The graph summary, its coverage counted in pages (never source-document nodes).

    ``total_pages`` is the scope's live page count (``wiki_stats``): with it the
    result is complete only when every page was shown.
    """
    data = data if isinstance(data, dict) else {}
    pages, _, _ = _graph_parts(data)
    if total_pages is not None:
        complete = len(pages) >= total_pages
    else:
        complete = len(pages) < GRAPH_MAX_NODES
    of_total = f" of {total_pages}" if total_pages is not None else ""
    return capped_result(
        graph(data, total_pages=total_pages),
        tool="wiki_graph",
        returned=len(pages),
        total=total_pages,
        complete=complete,
        hint=(
            f"Showing the {len(pages)} most linked-to pages{of_total} (the summary asks for "
            f"at most {GRAPH_MAX_NODES}): pass project to narrow it, or use knowledge_base "
            "command='links' on one page."
        ),
    )


def stats(data: dict) -> str:
    """``wiki_stats``: what each count counts.

    ``sources`` is the route's count of page-to-source citations, so one
    document cited by three pages counts three times; ``links`` is org-wide
    whatever the project, so it is not shown beside project counts.
    """
    data = data if isinstance(data, dict) else {}
    return (
        f"Pages: {data.get('pages', 0)} (live pages in scope)\n"
        f"Sources: {data.get('sources', 0)} (page-to-source citations: a document cited "
        "by three pages counts three times)"
    )
