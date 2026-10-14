"""Read answers, sources and proposal ids out of public API responses.

Public responses differ by route: research returns an answer with sources, the
context route returns rendered markdown with blocks, and Knowledge writes return
the door's ``{content: [{type, text}], ...}`` envelope. These helpers accept every
shape the public contract documents and fall back to the text of the response,
so a field renamed in a later minor version degrades a score instead of
crashing the run.
"""

from __future__ import annotations

import json
import re
from typing import Any

_ANSWER_KEYS = ("answer", "content", "text", "result", "response", "output")
_SOURCE_KEYS = ("sources", "citations", "references")
_PROPOSAL_RE = re.compile(r'"?proposal_id"?\s*[:=]\s*"?([0-9a-fA-F-]{8,})', re.I)


def _join_blocks(value: list[Any]) -> str:
    parts: list[str] = []
    for item in value:
        if isinstance(item, dict):
            parts.append(str(item.get("text") or item.get("content") or ""))
        else:
            parts.append(str(item))
    return "\n".join(part for part in parts if part)


def answer_text(response: Any) -> str:
    """The prose of an answer, from research, context or a door envelope."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, list):
        return _join_blocks(response)
    if isinstance(response, dict):
        for key in _ANSWER_KEYS:
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, list) and value:
                return _join_blocks(value)
            if isinstance(value, dict):
                nested = answer_text(value)
                if nested:
                    return nested
        blocks = response.get("blocks")
        if isinstance(blocks, list):
            return _join_blocks(blocks)
    return json.dumps(response, default=str)


def answer_sources(response: Any) -> list[Any]:
    if isinstance(response, dict):
        for key in _SOURCE_KEYS:
            value = response.get(key)
            if isinstance(value, list):
                return value
        for key in _ANSWER_KEYS:
            value = response.get(key)
            if isinstance(value, dict):
                nested = answer_sources(value)
                if nested:
                    return nested
    return []


def proposal_ids(response: Any) -> list[str]:
    """Every proposal id a write response mentions, in order, without repeats."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "proposal_id" and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            found.extend(_PROPOSAL_RE.findall(node))

    walk(response)
    unique: list[str] = []
    for value in found:
        if value not in unique:
            unique.append(value)
    return unique


def compound_status(conversation: Any) -> str | None:
    if isinstance(conversation, dict):
        status = conversation.get("compound_status")
        if isinstance(status, str):
            return status
        inner = conversation.get("conversation")
        if isinstance(inner, dict):
            return compound_status(inner)
    return None
