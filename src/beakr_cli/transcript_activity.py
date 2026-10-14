"""What a session did besides talk: the tools it called and what it read from Beakr.

Beakr keeps "summary plus references" of an imported session by default, and
two of those references come from tool calls rather than from the messages:

- ``tools_used``: every tool the session called, by name, with how often.
- ``sources_read``: the Beakr Knowledge it read, with the version read when the
  transcript shows it, so ``beakr conversations freshness`` can later say
  whether what the session concluded still rests on current Knowledge.

A read is recorded only from a call that read Beakr and the result it got back:

- the ``knowledge_base`` tool (Beakr's MCP server, under any server name) with
  ``cat`` or ``show``: the page id and revision come from the page header the
  result carries (``KB page ID: <id>`` and ``Rev: <n>``, or ``(revision <n>)``
  for a historical read);
- the ``read_file`` tool: a document read, at the file and version its
  result's header names (``file <id> version <id>``) or, in older
  transcripts, its structured result reports;
- ``beakr kb cat`` run in a shell: the page JSON it prints when piped (``id``,
  ``revision``); ``beakr files download <id>``: a document read whose version
  the output does not say.

A failed call, or a result that does not show what was read, records nothing:
a read is never guessed from what the session asked for.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

#: The engine accepts at most this many reads and tools per import.
MAX_SOURCES_READ = 500
MAX_TOOLS_USED = 100
_MAX_TOOL_NAME = 200

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_UUID_ONLY = re.compile(rf"^{_UUID}$")
_PAGE_ID = re.compile(rf"KB page ID:\s*({_UUID})")
_HEADER_REV = re.compile(r"\|\s*Rev:\s*(\d+)\s*(?:\||$)", re.MULTILINE)
_HISTORICAL_REV = re.compile(r"^# .*\(revision (\d+)\)\s*$", re.MULTILINE)
_KB_CAT = re.compile(r"\bbeakr\s+(?:kb|knowledge)\s+cat\b")
_KB_CAT_ID = re.compile(rf"\bbeakr\s+(?:kb|knowledge)\s+cat\s+['\"]?({_UUID})")
#: The page header ``cat`` prints sits right under the id; a table further down
#: the page can hold its own "| Rev: n |".
_HEADER_CHARS = 600
#: The header a read_file result opens with: "... ; file <id> version <id>)".
_FILE_READ_HEADER = re.compile(rf"\bfile ({_UUID}) version ({_UUID})\)")
_FILES_DOWNLOAD = re.compile(rf"\bbeakr\s+files\s+download\s+['\"]?({_UUID})")

_KB_READ_COMMANDS = frozenset({"cat", "show"})
_SHELL_ARGUMENT_KEYS = ("command", "cmd")


def short_tool_name(name: str) -> str:
    """``mcp__beakr__knowledge_base`` and ``beakr__knowledge_base`` -> ``knowledge_base``."""
    return name.rsplit("__", 1)[-1]


def _json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def result_text(output: Any) -> str:
    """The text of a tool result in any of the shapes hosts record it in."""
    if isinstance(output, str):
        parsed = _json(output)
        if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
            # Codex shell results: {"output": "...", "metadata": {...}}.
            return parsed["output"]
        return output
    if isinstance(output, list):
        return "\n".join(result_text(part) for part in output)
    if isinstance(output, dict):
        if output.get("type") == "text" and isinstance(output.get("text"), str):
            return output["text"]
        for key in ("content", "output", "text"):
            if key in output:
                return result_text(output[key])
    return ""


def _structured(output: Any) -> dict | None:
    """A structured MCP result, whether recorded as JSON text or as an object."""
    if isinstance(output, dict):
        for key in ("structuredContent", "structured_content"):
            if isinstance(output.get(key), dict):
                return output[key]
    parsed = _json(result_text(output)) if not isinstance(output, dict) else output
    return parsed if isinstance(parsed, dict) else None


def _shell_command(arguments: dict) -> str:
    for key in _SHELL_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(str(part) for part in value)
    return ""


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


class SessionActivity:
    """Collects tool calls and their results while a transcript is parsed."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._pending: dict[str, tuple[str, dict]] = {}
        self._reads: list[dict] = []
        self._read_keys: set[tuple] = set()

    # -- recording ------------------------------------------------------------

    def call(self, call_id: Any, name: Any, arguments: Any) -> None:
        """One tool call. ``arguments`` may be a dict or its JSON text."""
        if not isinstance(name, str) or not name.strip():
            return
        name = name.strip()[:_MAX_TOOL_NAME]
        self._counts[name] += 1
        parsed = _json(arguments) if isinstance(arguments, str) else arguments
        if isinstance(call_id, str) and call_id:
            self._pending[call_id] = (name, parsed if isinstance(parsed, dict) else {})

    def known(self, call_id: Any) -> bool:
        return isinstance(call_id, str) and call_id in self._pending

    def result(self, call_id: Any, output: Any, *, is_error: bool = False) -> None:
        """The result of an earlier call; reads are extracted from successful ones."""
        if not isinstance(call_id, str) or call_id not in self._pending:
            return
        name, arguments = self._pending.pop(call_id)
        if is_error:
            return
        tool = short_tool_name(name)
        if tool == "knowledge_base":
            self._knowledge_read(arguments, output)
        elif tool == "read_file":
            self._file_read(arguments, output)
        else:
            command = _shell_command(arguments)
            if command:
                self._shell_read(command, output)

    # -- extraction -----------------------------------------------------------

    def _add(self, record: dict) -> None:
        key = tuple(sorted(record.items()))
        if key in self._read_keys or len(self._reads) >= MAX_SOURCES_READ:
            return
        self._read_keys.add(key)
        self._reads.append(record)

    def _page(self, page_id: str, revision: int | None) -> None:
        record: dict[str, Any] = {"page_id": page_id.lower()}
        if revision:
            record["revision"] = revision
        self._add(record)

    def _knowledge_read(self, arguments: dict, output: Any) -> None:
        command = arguments.get("command")
        if command not in _KB_READ_COMMANDS:
            return
        structured = _structured(output)
        text = result_text(output)
        if structured is not None and isinstance(structured.get("content"), str):
            text = structured["content"]
        if structured is not None and structured.get("status") not in (None, "success"):
            return
        found = _PAGE_ID.search(text)
        nested = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
        named = arguments.get("page") or nested.get("page")
        rev = _positive_int(arguments.get("rev") or nested.get("rev"))
        if found:
            header = text[found.end() : found.end() + _HEADER_CHARS]
            revision = rev
            if revision is None:
                match = _HEADER_REV.search(header) or _HISTORICAL_REV.search(text[: found.start()])
                revision = int(match.group(1)) if match else None
            self._page(found.group(1), revision)
        elif isinstance(named, str) and _UUID_ONLY.match(named) and rev is not None:
            # ``show {page, rev}`` prints no id; the page was named by id.
            self._page(named, rev)

    def _file_read(self, arguments: dict, output: Any) -> None:
        header = _FILE_READ_HEADER.search(result_text(output)[:_HEADER_CHARS])
        if header:
            # The file read, whatever form of id the call passed (a version id,
            # a citation token).
            self._add(
                {"document_id": header.group(1).lower(), "version_id": header.group(2).lower()}
            )
            return
        artifact_id = arguments.get("artifact_id")
        if not isinstance(artifact_id, str) or not _UUID_ONLY.match(artifact_id.strip()):
            return
        record: dict[str, Any] = {"document_id": artifact_id.strip().lower()}
        structured = _structured(output)
        file_info = structured.get("file") if isinstance(structured, dict) else None
        if isinstance(file_info, dict):
            version = file_info.get("version_id")
            if isinstance(version, str) and _UUID_ONLY.match(version):
                record["version_id"] = version.lower()
        elif structured is None and not result_text(output).strip():
            return
        self._add(record)

    def _shell_read(self, command: str, output: Any) -> None:
        if _KB_CAT.search(command):
            text = result_text(output)
            start = text.find("{")
            page = _json(text[start:]) if start >= 0 else None
            if isinstance(page, dict):
                # A page prints its own ``id``; ``--rev`` prints a revision row
                # with no id, so the page is the one the command named by id.
                named = _KB_CAT_ID.search(command)
                page_id = page.get("page_id") or page.get("id") or (named and named.group(1))
                revision = _positive_int(page.get("revision"))
                if isinstance(page_id, str) and _UUID_ONLY.match(page_id):
                    self._page(page_id, revision)
            return
        download = _FILES_DOWNLOAD.search(command)
        if download and '"path"' in result_text(output):
            self._add({"document_id": download.group(1).lower()})

    # -- results --------------------------------------------------------------

    def tools_used(self) -> list[dict[str, Any]]:
        ordered = sorted(self._counts.items(), key=lambda item: (-item[1], item[0]))
        return [{"name": name, "count": count} for name, count in ordered[:MAX_TOOLS_USED]]

    def sources_read(self) -> list[dict[str, Any]]:
        return list(self._reads)
