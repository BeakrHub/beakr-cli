"""A wiki proposal's payload as a person reads it at a terminal.

``beakr kb proposals show`` (and the review step of ``accept``) used to dump the
payload as indented JSON: an edit arrived as two full copies of the page with
``\\n`` escapes, and the content hash a reviewer must copy was printed above it
and scrolled away. Here a content change is a unified diff, page text is shown
as text, and every other field is a labelled line. ``--json`` still prints the
payload unchanged.
"""

from __future__ import annotations

import difflib
from typing import Any

#: One rendered line and its Rich style (None for plain text).
Line = tuple[str, str | None]

#: Top-level payload keys the proposal header already shows, or bookkeeping a
#: reviewer does not act on.
_HEADER_KEYS = frozenset({"type", "summary", "page_title", "group_id", "project_id"})

#: Keys rendered as part of a content change rather than as their own lines.
_CHANGE_KEYS = frozenset({"current_content", "proposed_content", "current_title", "proposed_title"})

#: Lines of unchanged text kept around each changed hunk.
_DIFF_CONTEXT = 2

_DIFF_STYLES = {"+": "green", "-": "red", "@": "cyan"}


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_change(item: dict[str, Any]) -> bool:
    return isinstance(item.get("current_content"), str) and isinstance(
        item.get("proposed_content"), str
    )


def _diff_lines(current: str, proposed: str, indent: str) -> list[Line]:
    lines: list[Line] = []
    diff = difflib.unified_diff(
        current.splitlines(), proposed.splitlines(), lineterm="", n=_DIFF_CONTEXT
    )
    for line in diff:
        if line.startswith(("---", "+++")):
            continue  # file headers: there are no files here
        lines.append((f"{indent}{line}", _DIFF_STYLES.get(line[:1])))
    return lines or [(f"{indent}(no text change)", "dim")]


def _change_lines(item: dict[str, Any], indent: str) -> list[Line]:
    lines: list[Line] = []
    old_title, new_title = item.get("current_title"), item.get("proposed_title")
    if new_title and new_title != old_title:
        lines.append((f"{indent}Title: {old_title} -> {new_title}", None))
    lines.append((f"{indent}Changes:", "bold"))
    lines.extend(_diff_lines(item["current_content"], item["proposed_content"], indent + "  "))
    return lines


def _text_lines(text: str, indent: str) -> list[Line]:
    return [(f"{indent}{line}", None) for line in text.splitlines()] or [(indent, None)]


def _field_lines(key: str, value: Any, indent: str) -> list[Line]:
    label = _label(key)
    if isinstance(value, dict):
        return [(f"{indent}{label}:", "bold"), *_object_lines(value, indent + "  ")]
    if isinstance(value, list):
        lines: list[Line] = [(f"{indent}{label}:", "bold")]
        for entry in value:
            if isinstance(entry, dict):
                body = _object_lines(entry, indent + "    ")
                if body:
                    first, style = body[0]
                    lines.append((f"{indent}  - {first.lstrip()}", style))
                    lines.extend(body[1:])
            else:
                lines.append((f"{indent}  - {entry}", None))
        return lines
    if isinstance(value, bool):
        return [(f"{indent}{label}: {'yes' if value else 'no'}", None)]
    text = str(value)
    if "\n" in text:
        return [(f"{indent}{label}:", "bold"), *_text_lines(text, indent + "  ")]
    return [(f"{indent}{label}: {text}", None)]


def _object_lines(
    item: dict[str, Any], indent: str, *, skip: frozenset[str] = frozenset()
) -> list[Line]:
    lines: list[Line] = []
    change = _is_change(item)
    for key, value in item.items():
        if key in skip or _empty(value) or (change and key in _CHANGE_KEYS):
            continue
        lines.extend(_field_lines(key, value, indent))
    if change:
        lines.extend(_change_lines(item, indent))
    return lines


def payload_lines(payload: dict[str, Any]) -> list[Line]:
    """The payload's fields, a content change last as a diff, for a terminal."""
    return _object_lines(payload, "", skip=_HEADER_KEYS)
