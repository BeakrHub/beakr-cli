"""What this CLI adds to the SDK's public source record.

``beakr_sdk.sources.public_source`` is the one shape the SDK, the CLI and the
stdio MCP server share. The CLI pins a released SDK, so a field the engine's
public contract gains reaches this code before the SDK release that forwards
it. This module holds what is kept here in the meantime.
"""

from __future__ import annotations

from typing import Any

__all__ = ["native_read_call", "with_read_call"]


def native_read_call(retrieval: object) -> dict[str, Any] | None:
    """The call that re-reads a connected item that is not a file, or ``None``.

    The API attaches to a cited meeting note, email or Slack thread the provider
    read tool and that item's own id (``granola_get_note`` with ``note_id``); the
    Sources list prints it as ``read with integrations command='call' ...``
    (``mcp_render._read_call``). ``beakr_sdk.sources.public_source`` up to 0.2.x
    forwards a ``retrieval`` only when it carries a file download ``handle``, so
    the stdio server and ``beakr kb`` dropped the call and the line ended at the
    ``source_ref``. The rule is the hosted server's ``formatting.public_source``
    (a named tool, scalar arguments, nothing nested); the API has already
    limited the tool to a known read.
    """
    if not isinstance(retrieval, dict):
        return None
    tool = retrieval.get("tool")
    arguments = retrieval.get("arguments")
    if not isinstance(tool, str) or not tool or not isinstance(arguments, dict) or not arguments:
        return None
    if "handle" in arguments:
        return None
    clean: dict[str, Any] = {}
    for name, value in arguments.items():
        if not isinstance(name, str) or isinstance(value, bool):
            return None
        if not isinstance(value, (str, int)) or value == "":
            return None
        clean[name] = value
    return {"tool": tool, "arguments": clean}


def with_read_call(record: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """``record`` (a public source built from ``source``) with its read call, when it has one.

    A no-op once the installed SDK forwards the call itself.
    """
    if "retrieval" not in record and (call := native_read_call(source.get("retrieval"))):
        record["retrieval"] = call
    return record
