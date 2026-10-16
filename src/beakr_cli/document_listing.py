"""Fit canonical document listing rows before generic MCP text clipping.

Mirrored in beakr-cli and checked byte for byte. Only generated listing syntax
is interpreted; unrecognized or already-cut responses retain generic recovery.
Source rows and scan limits are preserved, never summarized or reconstructed.
"""

from __future__ import annotations

import re
from typing import Any

_SUMMARY = re.compile(
    r"^(?P<prefix>documents grep .+: (?P<total>\d+) matching documents?; showing )"
    r"(?P<start>\d+)-(?P<end>\d+)"
    r"(?P<suffix>\. (?:Searched \d+ of \d+ documents \(\d+ in scope\)"
    r"|Scanned \d+ of \d+ candidate documents after the safe prefilter; "
    r"\d+ documents considered \(\d+ in scope\))\.)$"
)
# Both readable API rows and legacy token rows end in the document's exact ID.
# Content-mode grep has a different summary and cannot enter this parser.
_ROW = re.compile(r".+  \(id [0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\)")
_MORE = re.compile(
    r"More documents follow: (?P<remaining>\d+) after these\. "
    r"Continue with offset=(?P<offset>\d+)\."
)
_NAVIGATION = (
    "To open a passage in a later API call, use documents read with the document's displayed id "
    "or path and start_line/end_line."
)
_NAVIGATION_FORMS = (
    _NAVIGATION,
    _NAVIGATION
    + " The rag tokens cite these search results; they are not reusable document identifiers.",
)


def fit_document_listing(text: str, limit: int) -> dict[str, Any] | None:
    """Return a whole-row window with an explicit offset, or decline the shape.

    Offsets count matches within the same backend scan, not catalog documents.
    Any after_document scan continuation must wait until that window is read.
    This performs no requests and makes no claim that the scan was exhaustive.
    """
    if len(text) <= limit or "[truncated:" in text:
        return None
    header, separator, body = text.partition("\n\n")
    if not separator:
        return None
    footer: list[str] = []
    for navigation in _NAVIGATION_FORMS:
        if body.endswith("\n\n" + navigation):
            body = body[: -(len(navigation) + 2)]
            footer = [navigation]
            break
    lines, rows = header.splitlines(), body.splitlines()
    if not lines:
        return None
    match = _SUMMARY.fullmatch(lines[0])
    if not match or not rows or not all(_ROW.fullmatch(row) for row in rows):
        return None
    total, start, requested_end = (int(match[key]) for key in ("total", "start", "end"))
    rendered_end = start + len(rows) - 1
    if not 1 <= start <= rendered_end <= requested_end <= total:
        return None
    continuations = [m for line in lines[1:] if (m := _MORE.fullmatch(line))]
    if len(continuations) > 1:
        return None
    if continuations:
        continuation = continuations[0]
        if (
            int(continuation["offset"]) != rendered_end
            or int(continuation["remaining"]) != total - rendered_end
        ):
            return None
    elif rendered_end != requested_end:
        # An upstream row budget can shorten the requested window, but only its
        # explicit continuation confirms that the printed rows form a prefix.
        return None
    notices = [line for line in lines[1:] if not _MORE.fullmatch(line)] + footer
    for count in range(len(rows) - 1, 0, -1):
        end = start + count - 1
        summary = f"{match['prefix']}{start}-{end}{match['suffix']}"
        more = f"More documents follow: {total - end} after these. Continue with offset={end}."
        hint = (
            f"MCP output window: {count} complete rows visible; backend supplied {len(rows)} rows. "
            f"Continue the same project, query and filters at offset={end} before advancing "
            "any after_document scan cursor. Counts retain the backend's scan scope."
        )
        fitted = "\n".join([summary, *notices, more, hint, "", *rows[:count]])
        if len(fitted) <= limit:
            return {
                "content": fitted,
                "coverage": {
                    "complete": False,
                    "has_more": True,
                    "truncated": True,
                    "returned": count,
                    "next_cursor": None,
                    "visible_start": start,
                    "visible_end": end,
                    "backend_rendered_end": rendered_end,
                    "backend_requested_end": requested_end,
                    "next_offset": end,
                    "document_window_version": 1,
                },
                "next": hint,
            }
    return None
