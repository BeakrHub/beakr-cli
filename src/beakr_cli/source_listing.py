"""Where a page's sources live, for ``beakr kb sources`` and ``beakr kb cat``.

A page cites the same document many times (one citation per claim), so the
source list is grouped by document: one entry per file or link, saying where
it lives (``filename · provider · url-or-path``, or for a file only Beakr
holds, its file id and the ``beakr files download`` command) and how many
citations point at it. ``--json`` keeps every citation as its public source
record (:mod:`beakr_sdk.sources`).
"""

from __future__ import annotations

from typing import Any

from beakr_sdk.sources import source_where


def _document_key(record: dict[str, Any]) -> tuple[str, ...]:
    location = record.get("location") or {}
    return (
        str(location.get("file_id") or ""),
        str(location.get("url") or ""),
        str(location.get("display_path") or ""),
        str(record.get("filename") or record.get("name") or ""),
    )


def group_by_document(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per document, in order of first citation.

    Each entry is ``{record, where, citations, stances, sections}``: the first
    citation's record, the where line, and what all its citations share.
    """
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in records:
        group = groups.setdefault(
            _document_key(record),
            {
                "record": record,
                "where": source_where(record),
                "citations": 0,
                "stances": [],
                "sections": [],
            },
        )
        group["citations"] += 1
        stance = record.get("stance")
        if stance and stance not in group["stances"]:
            group["stances"].append(stance)
        section = (record.get("section") or {}).get("title") or (record.get("section") or {}).get(
            "id"
        )
        if section and section not in group["sections"]:
            group["sections"].append(section)
    return list(groups.values())


def document_lines(records: list[dict[str, Any]]) -> list[str]:
    """Compact lines: ``n<TAB>where<TAB>citations=N<TAB>stance=...<TAB>sections=...``."""
    lines = []
    for index, group in enumerate(group_by_document(records), 1):
        columns = [f"[{index}]", group["where"], f"citations={group['citations']}"]
        if group["stances"]:
            columns.append(f"stance={','.join(group['stances'])}")
        if group["sections"]:
            columns.append(f"sections={','.join(group['sections'])}")
        lines.append("\t".join(columns))
    return lines
