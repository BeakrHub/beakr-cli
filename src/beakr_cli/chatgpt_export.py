"""Read a ChatGPT data export into sessions for import (client ``chatgpt-export``).

ChatGPT's "Export data" gives a zip whose ``conversations.json`` is a list of
conversations. Each conversation is a tree, because editing a message or
regenerating a reply branches it:

- ``mapping``: ``{node id: {"message", "parent", "children"}}``;
- ``current_node``: the leaf of the branch the person last saw.

The conversation as the person last saw it is the path from the root to
``current_node``; the other branches are drafts they moved away from, and are
not imported. Either the zip or the extracted ``conversations.json`` is read.

What is kept, as for any transcript (``transcripts``): the visible text of
user and assistant turns. Dropped: system messages, hidden context (custom
instructions, ``is_visually_hidden_from_conversation``), assistant turns
addressed to a tool (code sent to the Python tool, a browsing query), and tool
output. Those tool calls are still counted for ``tools_used``.

Authorship: an export records no marker that a person typed a given turn
(scheduled tasks and agent runs write conversations too), so user turns are
``unknown``, never ``human``, and the session is never classified human.
Imported-session learning therefore never quotes an exported conversation.
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from beakr_cli.transcripts import (
    AUTHOR_AGENT,
    AUTHOR_UNKNOWN,
    UNKNOWN,
    ParsedSession,
    _add_message,
    _title_from,
)

_EXPORT_MEMBER = "conversations.json"
_TEXT_CONTENT_TYPES = frozenset({"text", "multimodal_text"})
#: How many tools are reported per conversation (the engine's limit).
_MAX_TOOLS = 100


class ChatGPTExportError(ValueError):
    """The file is not a ChatGPT export this version can read."""


def load_conversations(path: str | Path) -> list[dict[str, Any]]:
    """The conversations in an export zip or its ``conversations.json``."""
    path = Path(path)
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = [n for n in archive.namelist() if Path(n).name == _EXPORT_MEMBER]
                if not names:
                    raise ChatGPTExportError(f"{path} has no {_EXPORT_MEMBER}.")
                data = json.loads(archive.read(min(names, key=len)).decode("utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, ChatGPTExportError):
            raise
        raise ChatGPTExportError(f"{path} is not a readable ChatGPT export: {exc}") from exc
    if isinstance(data, dict):
        # A single conversation saved on its own.
        data = [data]
    if not isinstance(data, list):
        raise ChatGPTExportError(f"{path} is not a ChatGPT export (expected a list).")
    return [c for c in data if isinstance(c, dict) and isinstance(c.get("mapping"), dict)]


def _iso(timestamp: Any) -> str | None:
    if isinstance(timestamp, bool) or not isinstance(timestamp, int | float):
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _branch(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    """Messages on the path from the root to ``current_node``, oldest first."""
    mapping: dict[str, Any] = conversation["mapping"]
    node_id = conversation.get("current_node")
    if node_id not in mapping:
        # No current node recorded: the newest leaf is what was last seen.
        leaves = [k for k, v in mapping.items() if isinstance(v, dict) and not v.get("children")]
        node_id = max(leaves, key=lambda k: _node_time(mapping[k]), default=None)
    path: list[dict[str, Any]] = []
    seen: set[str] = set()
    while isinstance(node_id, str) and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if not isinstance(node, dict):
            break
        if isinstance(node.get("message"), dict):
            path.append(node["message"])
        node_id = node.get("parent")
    path.reverse()
    return path


def _node_time(node: Any) -> float:
    message = node.get("message") if isinstance(node, dict) else None
    created = message.get("create_time") if isinstance(message, dict) else None
    return float(created) if isinstance(created, int | float) else 0.0


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, dict) or content.get("content_type") not in _TEXT_CONTENT_TYPES:
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    # Images and files in a multimodal message are objects; only text is kept.
    return "\n\n".join(p for p in parts if isinstance(p, str) and p.strip())


def _hidden(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("is_visually_hidden_from_conversation"))


def parse_chatgpt_conversation(conversation: dict[str, Any]) -> ParsedSession | None:
    """One conversation as a session; ``None`` when it has no id."""
    session_id = conversation.get("conversation_id") or conversation.get("id")
    if not isinstance(session_id, str) or not session_id:
        return None
    messages: list[dict[str, Any]] = []
    tool_calls: Counter[str] = Counter()
    tool_outputs: Counter[str] = Counter()
    for message in _branch(conversation):
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        role = author.get("role")
        recipient = message.get("recipient")
        if role == "tool":
            if isinstance(author.get("name"), str) and author["name"]:
                tool_outputs[author["name"]] += 1
            continue
        if role == "assistant" and isinstance(recipient, str) and recipient not in ("all", ""):
            # Addressed to a tool (code for the Python tool, a browsing query).
            tool_calls[recipient] += 1
            continue
        if role not in ("user", "assistant") or _hidden(message):
            continue
        author_kind = AUTHOR_AGENT if role == "assistant" else AUTHOR_UNKNOWN
        _add_message(
            messages, role, _text(message), _iso(message.get("create_time")), author=author_kind
        )
    # Calls are the count of uses; a tool whose calls the export did not keep
    # is counted by its outputs instead.
    counts = tool_calls + Counter({k: v for k, v in tool_outputs.items() if k not in tool_calls})
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:_MAX_TOOLS]
    title = conversation.get("title")
    if isinstance(title, str) and title.strip():
        title = title.strip()[:500]
    else:
        title = _title_from(messages)
    return ParsedSession(
        session_id=session_id,
        messages=messages,
        files_touched=[],
        cwd=None,
        title=title,
        classification=UNKNOWN,
        tools_used=[{"name": name, "count": count} for name, count in ordered],
        sources_read=[],
    )


def parse_chatgpt_export(path: str | Path) -> list[ParsedSession]:
    """Every conversation in the export that has messages, in the export's order."""
    sessions = []
    for conversation in load_conversations(path):
        session = parse_chatgpt_conversation(conversation)
        if session is not None and session.messages:
            sessions.append(session)
    return sessions
