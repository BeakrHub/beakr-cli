"""What the ``integrations`` MCP tool answers, on both MCP servers.

``integrations`` reads the caller's connected integrations (Slack, Drive,
Gmail, Jira, ...) with the read-only connector tools Beakr's own agent uses,
through ``GET /v1/integrations/tools`` and
``POST /v1/integrations/tools/{tool_name}/call``; the routes decide what a call
may reach, and this module turns their answers into the text an agent reads.

A byte-for-byte copy of the engine's
``beakr/api/mcp_remote/integration_results.py`` from ``COMMANDS`` down, so the
hosted and stdio servers answer the same text;
``tests/test_mcp_integration_results_match_engine.py`` fails when they differ.
"""

from __future__ import annotations

import json
from typing import Any

COMMANDS = ("list", "describe", "call")

#: The options that page ``list``. A connector tool takes its own ``limit`` or
#: ``cursor`` inside ``arguments``, so one given beside a call is refused rather
#: than dropped without a word.
LIST_OPTIONS = ("provider", "cursor", "limit")

#: The tool's text kept in a call result; the rest of the text limit is the
#: Sources list, which must survive a long result.
CONTENT_CHARS = 15_000
#: The Sources list stays under this; the count of the rest is named.
SOURCES_CHARS = 4_000
#: Argument names a list line shows before "+N more".
LIST_ARGUMENTS = 8
#: A list line's description: its first sentence, at most this long.
SUMMARY_CHARS = 160

_CALL_SHAPE = (
    'integrations {"command": "call", "tool": "<name from list>", '
    '"arguments": {...the tool\'s own options...}, "connector_id": "<optional>"}'
)

#: Fixes in this tool's terms for the codes the integration-tool routes answer.
FIXES: dict[str, str] = {
    "tool_not_found": (
        "Call integrations command='list' for the tools you can call, and use one of those names."
    ),
    "tool_not_read_only": (
        "Only read-only tools can be called here. Make the change in Beakr, where the "
        "person approves it."
    ),
    "integration_not_connected": (
        "Call integrations command='list' for the integrations and connector_ids you can "
        "use; an integration missing there must be connected in Beakr first."
    ),
    "invalid_tool_arguments": (
        "Call integrations command='describe' with this tool for its input schema, then "
        "send `arguments` that match it."
    ),
    "unsupported_provider": (
        "Pass a provider as integrations command='list' prints it (for example "
        "slack:nango), or omit provider."
    ),
    "integration_tool_failed": (
        "Retry shortly; if it keeps failing, the integration may need to be reconnected in Beakr."
    ),
}

#: ``error_code`` of a failure the tool itself reported (the provider refused, the
#: item is outside what the integration may read).
TOOL_REPORTED_ERROR = "tool_reported_error"


def error(code: str | None, message: str, fix: str | None = None) -> dict[str, Any]:
    """A refused call, sent as ``{status, error_code, message, fix}`` marked isError."""
    return {"status": "error", "error_code": code, "message": message, "fix": fix}


def refusal(
    code: str | None, message: str, fix: str | None = None, request_id: str | None = None
) -> dict[str, Any]:
    """A route's refusal, with the fix in this tool's terms when the code has one."""
    body = error(code, message, FIXES.get(code or "") or fix)
    if request_id:
        body["request_id"] = request_id
    return body


def _given(value: Any) -> bool:
    return value not in (None, "", {}, [])


def misuse(args: dict[str, Any]) -> dict[str, Any] | None:
    """The refusal for options that do not belong to the command, or None."""
    command = str(args.get("command") or "")
    if command not in COMMANDS:
        return error(
            "invalid_arguments",
            f"integrations has no command {command!r}.",
            f"Pass command 'list', 'describe' or 'call': {_CALL_SHAPE}",
        )
    tool = str(args.get("tool") or "").strip()
    if command == "list":
        if tool:
            return error(
                "invalid_arguments",
                "command='list' lists every tool you can call; it takes no `tool`.",
                "Pass `tool` with command='describe' for its schema, or command='call' to run it.",
            )
        if _given(args.get("arguments")):
            return error(
                "invalid_arguments",
                "command='list' takes no `arguments`: they are a called tool's own options.",
                f"Call the tool with them: {_CALL_SHAPE}",
            )
        return None
    if not tool:
        return error(
            "invalid_arguments",
            f"command='{command}' needs `tool`: a tool name from integrations command='list'.",
            f"Send the call in this shape: {_CALL_SHAPE}",
        )
    misplaced = [key for key in LIST_OPTIONS if _given(args.get(key))]
    if misplaced:
        names = ", ".join(f"`{key}`" for key in misplaced)
        return error(
            "invalid_arguments",
            f"{names} {'is' if len(misplaced) == 1 else 'are'} for command='list' only; "
            "nothing was run.",
            "Put the tool's own options (its limit, cursor, ...) inside `arguments`: "
            f"{_CALL_SHAPE}",
        )
    if command == "describe" and _given(args.get("arguments")):
        return error(
            "invalid_arguments",
            "command='describe' takes no `arguments`; nothing was run.",
            f"Run the tool with them: {_CALL_SHAPE}",
        )
    return None


def _one_line(value: Any, width: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _summary(description: Any) -> str:
    """A tool description's first sentence, on one line."""
    text = str(description or "").strip()
    first = text.split("\n", 1)[0]
    end = first.find(". ")
    return _one_line(first if end < 0 else first[: end + 1], SUMMARY_CHARS)


def _argument_names(schema: Any) -> str:
    """``{query, channel?, limit?}``: the tool's options, the optional ones marked."""
    schema = schema if isinstance(schema, dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or ())
    names = [name if name in required else f"{name}?" for name in properties]
    shown = names[:LIST_ARGUMENTS]
    if len(names) > LIST_ARGUMENTS:
        shown.append(f"+{len(names) - LIST_ARGUMENTS} more")
    return "{" + ", ".join(shown) + "}"


def _connections(row: dict[str, Any]) -> str:
    connections = [c for c in row.get("connections") or [] if isinstance(c, dict)]
    labels = [
        f"{_one_line(c.get('label'), 80)} (connector_id {c.get('connector_id')})"
        if c.get("label")
        else f"connector_id {c.get('connector_id')}"
        for c in connections
    ]
    if len(labels) > 1:
        return f"{len(labels)} accounts: {'; '.join(labels)}. Pass connector_id to pick one."
    return labels[0] if labels else "no connected account"


def tool_list(rows: list[dict[str, Any]]) -> str:
    """One page of ``GET /v1/integrations/tools``, a line per tool under its integration."""
    if not rows:
        return (
            "No integration tools: none of the integrations this connection may read is "
            "connected. Connect one in Beakr, then list again."
        )
    lines = ["Integration tools you can call (read-only), by connected integration:"]
    provider = None
    for row in rows:
        if row.get("provider") != provider:
            provider = row.get("provider")
            lines.append(f"\n{provider} -- {_connections(row)}")
        lines.append(
            f"- {row.get('name')} {_argument_names(row.get('input_schema'))}: "
            f"{_summary(row.get('description'))}"
        )
    lines.append(
        "\nCall one with command='call', its name as `tool` and its options in `arguments`; "
        "command='describe' with `tool` gives its full description and input schema."
    )
    return "\n".join(lines)


def find(rows: list[dict[str, Any]], tool: str) -> dict[str, Any] | None:
    """The listed tool named ``tool``, or None."""
    return next((row for row in rows if row.get("name") == tool), None)


def not_listed(tool: str) -> dict[str, Any]:
    """``describe`` for a tool none of the caller's connected integrations has."""
    return error(
        "tool_not_found",
        f"No integration tool named {tool!r} is available to you: none of the integrations "
        "you may read here has it.",
        FIXES["tool_not_found"],
    )


def tool_detail(row: dict[str, Any]) -> dict[str, Any]:
    """``describe``: one tool's full description, connections and input schema."""
    schema = row.get("input_schema") if isinstance(row.get("input_schema"), dict) else {}
    text = "\n".join(
        [
            f"{row.get('name')} ({row.get('provider')})",
            f"Connections: {_connections(row)}",
            "",
            str(row.get("description") or "").strip(),
            "",
            "input_schema: " + json.dumps(schema, separators=(",", ":"), ensure_ascii=False),
            "",
            f"Run it: integrations command='call' tool='{row.get('name')}' "
            "arguments={...options from input_schema...}",
        ]
    )
    return {"content": text, "tool": row.get("name"), "provider": row.get("provider")}


def _source_line(source: dict[str, Any]) -> str:
    location = source.get("location") if isinstance(source.get("location"), dict) else {}
    key = source.get("citation_key")
    parts = [_one_line(source.get("name") or source.get("filename") or "source", 160)]
    for value in (source.get("provider"), location.get("url")):
        if value:
            parts.append(str(value))
    if source.get("source_ref"):
        parts.append(f"source_ref {source['source_ref']}")
    head = f"{{{{{key}}}}} " if key else ""
    return f"- {head}" + " · ".join(parts)


def sources_block(sources: list[Any]) -> str:
    """``Sources (N):`` then a line per item: its citation key, name, provider, url, source_ref."""
    records = [source for source in sources if isinstance(source, dict)]
    if not records:
        return ""
    lines = [
        f"Sources ({len(records)}; cite one in a Knowledge write by its source_ref):",
    ]
    used = len(lines[0])
    shown = 0
    for record in records:
        line = _source_line(record)
        if shown and used + len(line) + 1 > SOURCES_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
    if shown < len(records):
        lines.append(f"+{len(records) - shown} more sources not listed.")
    return "\n".join(lines)


def call_result(data: dict[str, Any]) -> dict[str, Any]:
    """``call``: the tool's text, cut to leave room for its Sources, then the Sources."""
    content = data.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    text = text.strip()
    if data.get("is_error"):
        return error(
            TOOL_REPORTED_ERROR,
            text or f"{data.get('tool')} reported a failure.",
            "Read the message: change the arguments, or pick an item the integration may read.",
        )
    sources = [source for source in data.get("sources") or [] if isinstance(source, dict)]
    coverage: dict[str, Any] = {}
    hints: list[str] = []
    if len(text) > CONTENT_CHARS:
        cut = len(text) - CONTENT_CHARS
        text = text[:CONTENT_CHARS].rstrip() + f"\n\n[cut: {cut:,} more characters of the result]"
        coverage.update(complete=False, truncated=True)
        hints.append(
            f"The tool's text was cut at {CONTENT_CHARS:,} characters: call it again asking "
            "for less (a smaller limit, a narrower query or date range)."
        )
    listing = sources_block(sources)
    return {
        "content": (text or "(the tool returned no text)") + (f"\n\n{listing}" if listing else ""),
        "coverage": coverage,
        "next": hints,
        "tool": data.get("tool"),
        "provider": data.get("provider"),
        "connector_ids": list(data.get("connector_ids") or []),
        "is_error": False,
        "sources": sources,
    }
