"""Forgiving tool arguments and compact tool errors for the stdio MCP server.

Agents call these tools the way they would call any other: options at the top
level, ``question`` for a question, ``scope`` for a project. Each of those used
to fail -- ``knowledge_base`` answered "Usage: grep <query>" when ``query`` sat
beside ``command`` instead of inside ``arguments``, and ``research`` answered a
pydantic dump ending in an errors.pydantic.dev link -- so the agent paid a
retry, sometimes several, to learn the calling convention.

Three things happen here, all before or after the tool runs and none of them
visible in the input schema (which stays small):

* :func:`normalize_arguments` folds a Knowledge tool's top-level options into
  ``arguments`` (explicit ``arguments`` keys win) and renames known aliases.
* :func:`validation_message` turns a pydantic validation error into one line
  naming what is missing and the call that works.
* :func:`compact_error` reduces an error result to ``{status, error_code,
  message, fix}``: no null coverage fields, no generic hints repeated per call.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

#: Tools whose options travel in one object parameter: top-level keys the tool
#: does not declare are folded into it.
FOLDED_OPTIONS: dict[str, str] = {
    "knowledge_base": "arguments",
    "knowledge_base_write": "arguments",
}

#: Names agents reach for, mapped to the parameter the tool declares.
ALIASES: dict[str, dict[str, str]] = {
    "research": {"question": "query", "q": "query", "scope": "project"},
    "knowledge_base": {"cmd": "command", "project": "scope"},
    "knowledge_base_write": {"project": "scope"},
}


def normalize_arguments(
    tool: str, arguments: dict[str, Any] | None, declared: set[str]
) -> dict[str, Any]:
    """``arguments`` with aliases renamed and undeclared Knowledge options folded in."""
    result = dict(arguments or {})
    for alias, target in ALIASES.get(tool, {}).items():
        if alias in result and alias not in declared:
            value = result.pop(alias)
            result.setdefault(target, value)
    container = FOLDED_OPTIONS.get(tool)
    if container is None:
        return result
    loose = {key: result.pop(key) for key in list(result) if key not in declared}
    if not loose:
        return result
    nested = result.get(container)
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except ValueError:
            return {**result, **loose}
    if nested is not None and not isinstance(nested, dict):
        return {**result, **loose}
    # Explicit ``arguments`` keys win over the same key given at the top level.
    result[container] = {**loose, **(nested or {})}
    return result


def _example(tool: str, schema: dict[str, Any]) -> str:
    required = list(schema.get("required") or [])
    example = {name: "..." for name in required}
    if tool in FOLDED_OPTIONS:
        example[FOLDED_OPTIONS[tool]] = {"query": "..."} if tool == "knowledge_base" else {}
    return json.dumps(example)


def validation_message(
    tool: str, error: ValidationError, schema: dict[str, Any], given: dict[str, Any]
) -> str:
    """One line: what was wrong with the call and the call that works."""
    properties = set((schema.get("properties") or {}).keys())
    problems: list[str] = []
    for item in error.errors():
        field = ".".join(str(part) for part in item.get("loc") or ()) or "arguments"
        if item.get("type") == "missing":
            problems.append(f"missing `{field}`")
        else:
            problems.append(f"`{field}`: {item.get('msg')}")
    line = f"{tool}: {'; '.join(problems) or 'invalid arguments'} -- pass {_example(tool, schema)}"
    optional = sorted(properties - set(schema.get("required") or []))
    if optional:
        line += f" (optional: {', '.join(optional)})"
    unknown = sorted(set(given) - properties)
    if unknown:
        line += f"; ignored unknown {', '.join(f'`{key}`' for key in unknown)}"
    return line


_USAGE = re.compile(r"^Usage: (?P<command>\S+)(?P<args>(?: <[\w-]+>)*)")

#: The engine's generic hint for a refused call; the message already says it.
_GENERIC_FIXES = frozenset({"Correct the call as the message says and retry; nothing was read."})


def _usage_fix(tool: str, message: str) -> str | None:
    """For a Knowledge usage error, the call that works, e.g. ``arguments: {"query": ...}``."""
    match = _USAGE.match(message.strip())
    if not match or tool not in FOLDED_OPTIONS:
        return None
    names = re.findall(r"<([\w-]+)>", match.group("args"))
    selector = "command" if tool == "knowledge_base" else "action"
    call = {selector: match.group("command"), "arguments": {name: "..." for name in names}}
    return f"pass {json.dumps(call)}"


def compact_error(tool: str, body: dict[str, Any]) -> dict[str, Any]:
    """An error result as ``{status, error_code, message, fix}``."""
    message = str(body.get("message") or body.get("content") or "").strip()
    hints = [str(hint) for hint in body.get("next") or [] if str(hint) not in _GENERIC_FIXES]
    fix = body.get("fix") or _usage_fix(tool, message) or (" ".join(hints) or None)
    compact: dict[str, Any] = {"status": "error"}
    if body.get("error_code"):
        compact["error_code"] = body["error_code"]
    compact["message"] = message or "The call failed."
    if fix:
        compact["fix"] = fix
    request_id = body.get("request_id")
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
        compact["request_id"] = request_id
    return compact
