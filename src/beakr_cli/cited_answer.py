"""Render answers that cite sources with ``{{key}}`` tokens, for a person at a terminal.

``beakr ask`` (a streamed agent run), ``beakr research`` and ``beakr kb research``
all receive answer text whose citations are ``{{key}}`` tokens, plus the cited
sources keyed by that same citation key. Each token becomes a numbered ``[n]``
reference in order of first use, and the answer is followed by a ``Sources:``
list with one line per number, so the three commands render citations the same
way.

A research answer from a current engine comes already readable: ``text`` cites
each passage by its readable reference in parentheses (``(memo.pdf › p. 7)``),
``citations`` holds one record per cited document (brief excerpts), and ``next``
says how to read what was trimmed. Its Sources list, one line per document, is
held to the hosted MCP server's ``SOURCES_CHARS``, with a line saying how many
sources were left out. An engine that still numbered ``text`` as ``[n]`` gets
a numbered list, and an older engine's ``answer``/``sources`` form is numbered
here as before.
"""

from __future__ import annotations

import json
import re
from typing import Any

from beakr_cli.mcp_render import SOURCES_CHARS
from beakr_cli.output import console, print_markdown

CITATION_TOKEN = re.compile(r"\{\{([^}]+)\}\}")


def research_citations(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The engine's per-document ``citations``, or ``None`` for an older engine's answer."""
    citations = data.get("citations")
    if isinstance(citations, list) and isinstance(data.get("text"), str):
        return [record for record in citations if isinstance(record, dict)]
    return None


#: ``[3]``: a citation an engine numbered before citations read as references.
_NUMBERED = re.compile(r"\[\d+\]")


def numbered_citations(
    citations: list[dict[str, Any]], separator: str = " ", *, text: str | None = None
) -> list[str]:
    """``[n] where`` per cited document, within ``SOURCES_CHARS``, then how many were left out.

    When ``text`` cites by readable reference rather than ``[n]`` (a current
    engine), each line is ``-`` and where the document lives: there is no number
    for it to match.
    """
    from beakr_sdk.sources import public_source, source_where

    numbered = text is None or bool(_NUMBERED.search(text))
    lines: list[str] = []
    used = 0
    for number, record in enumerate(citations, 1):
        mark = f"[{number}]" if numbered else "-"
        line = f"{mark}{separator}{source_where(public_source(record))}"
        if number > 1 and used + len(line) + 1 > SOURCES_CHARS:
            lines.append(f"+{len(citations) - number + 1} more sources (--json lists them all)")
            break
        lines.append(line)
        used += len(line) + 1
    return lines


def number_citations(text: str, order: list[str]) -> str:
    """Turn ``{{key}}`` citation tokens into ``[n]`` in order of first use.

    ``order`` collects each key the first time it is seen; pass the same list
    across several calls (a streamed answer) to keep the numbering stable.
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in order:
            order.append(key)
        return f"[{order.index(key) + 1}]"

    return CITATION_TOKEN.sub(replace, text)


def print_cited_sources(order: list[str], sources: dict[str, dict[str, Any]]) -> None:
    """The ``Sources:`` list for the numbered references in ``order``.

    Each line says where the source lives (:func:`beakr_sdk.sources.source_where`):
    filename and provider, a link or path to the original, or for a file only
    Beakr holds, its file id and the ``beakr files download`` command.
    """
    from beakr_sdk.sources import public_source, source_where

    if not order:
        return
    console.print("\n[bold]Sources:[/bold]")
    for index, key in enumerate(order, 1):
        source = sources.get(key)
        where = source_where(public_source(source, citation_key=key)) if source else key
        console.print(f"  [{index}] {where}", highlight=False, markup=False, soft_wrap=True)


def _research_details(data: dict[str, Any]) -> list[str]:
    lines = [
        f"{label}: {data[key]}"
        for key, label in (("checked", "Checked"), ("not_checked", "Not checked"))
        if data.get(key)
    ]
    if data.get("stopped_early"):
        lines.append("Research stopped early; coverage is incomplete.")
    if isinstance(data.get("coverage"), dict):
        lines.append("Coverage: " + json.dumps(data["coverage"], ensure_ascii=False))
    usage = {
        key: data[key]
        for key in ("cost_usd", "duration_s", "engine", "lead_model", "turns", "subtasks")
        if key in data
    }
    if usage:
        lines.append("Research usage: " + json.dumps(usage, ensure_ascii=False))
    return lines


def research_lines(data: dict[str, Any]) -> list[str]:
    """A research answer for an agent: the text with its citations, then one
    line per cited source saying where it lives (no terminal formatting)."""
    from beakr_sdk.sources import public_source, source_where

    from beakr_cli.research_metadata import with_cited_labels

    data = with_cited_labels(data)
    citations = research_citations(data)
    if citations is not None:
        lines = [str(data["text"]).rstrip()]
        if citations:
            lines += [
                "",
                "sources:",
                *numbered_citations(citations, separator="\t", text=str(data["text"])),
            ]
        if data.get("next"):
            lines += ["", "next:", *(str(hint) for hint in data["next"])]
        details = [f"{k}={data[k]}" for k in ("confidence", "gaps") if data.get(k)]
        if details:
            lines += ["", "\t".join(" ".join(str(d).split()) for d in details)]
        return lines + _research_details(data)

    order: list[str] = []
    lines = [number_citations(str(data.get("answer") or ""), order).rstrip()]
    raw_sources = data.get("sources")
    sources: dict[str, Any] = raw_sources if isinstance(raw_sources, dict) else {}
    if order:
        lines += ["", "sources:"]
        for index, key in enumerate(order, 1):
            source = sources.get(key)
            where = source_where(public_source(source, citation_key=key)) if source else key
            lines.append(f"[{index}]\t{where}")
    details = [f"{k}={data[k]}" for k in ("confidence", "gaps") if data.get(k)]
    if details:
        lines += ["", "\t".join(" ".join(str(d).split()) for d in details)]
    return lines + _research_details(data)


def print_research_result(data: dict[str, Any]) -> None:
    """A ``POST /v1/knowledge/research`` answer: text, its sources, confidence, gaps.

    A current engine sends readable ``text`` with one ``citations`` record per
    document; an older one sends ``answer`` with ``{{key}}`` tokens and
    ``sources`` keyed by citation key, numbered here.
    """
    from beakr_cli.research_metadata import with_cited_labels

    data = with_cited_labels(data)
    citations = research_citations(data)
    if citations is not None:
        if data["text"]:
            print_markdown(str(data["text"]))
        if citations:
            console.print("\n[bold]Sources:[/bold]")
            for line in numbered_citations(citations, text=str(data["text"])):
                console.print(f"  {line}", highlight=False, markup=False, soft_wrap=True)
        for hint in data.get("next") or []:
            console.print(f"\n{hint}", style="dim", highlight=False, markup=False)
    else:
        order: list[str] = []
        answer = number_citations(str(data.get("answer") or ""), order)
        if answer:
            print_markdown(answer)
        sources = data.get("sources")
        print_cited_sources(order, sources if isinstance(sources, dict) else {})

    details = []
    if data.get("confidence"):
        details.append(f"Confidence: {data['confidence']}")
    if data.get("gaps"):
        details.append(f"Gaps: {data['gaps']}")
    if details:
        console.print(f"\n[dim]{' | '.join(details)}[/dim]")

    for detail in _research_details(data):
        console.print(detail, highlight=False, markup=False)
