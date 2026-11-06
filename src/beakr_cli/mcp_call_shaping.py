"""Forgiving tool arguments and compact tool errors for the stdio MCP server.

Agents call these tools the way they would call any other: options at the top
level, ``question`` for a question, ``scope`` for a project. Each of those used
to fail -- ``knowledge_base`` answered "Usage: grep <query>" when ``query`` sat
beside ``command`` instead of inside ``arguments``, and ``research`` answered a
pydantic dump ending in an errors.pydantic.dev link -- so the agent paid a
retry, sometimes several, to learn the calling convention.

Four things happen here, all before or after the tool runs and none of them
visible in the input schema (which stays small):

* :func:`normalize_arguments` folds a Knowledge tool's top-level options into
  ``arguments`` (explicit ``arguments`` keys win) and renames known aliases;
  :func:`unknown_arguments` then refuses any argument the tool does not take.
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
    # A called integration tool's options sent beside ``tool`` are its arguments.
    "integrations": "arguments",
}

#: Names agents reach for, mapped to the parameter the tool declares. The names
#: of a project are handled apart, by :data:`PROJECT_PARAMETER`.
ALIASES: dict[str, dict[str, str]] = {
    "research": {"question": "query", "q": "query"},
    "knowledge_base": {"cmd": "command"},
}

#: Every name a project goes by on this server's tools.
PROJECT_NAMES = ("scope", "project", "project_id")

#: Each tool that takes a project, and the parameter that holds it. The Knowledge
#: tools take ``scope``, the others ``project`` (research and the graph tools
#: also an older ``project_id``), so an agent carrying ``scope`` across from a
#: Knowledge read had it dropped, and the tool answered for every project
#: (Q18-1). Any of the three names is read as the project on every one of them.
PROJECT_PARAMETER: dict[str, str] = {
    "knowledge_base": "scope",
    "knowledge_base_write": "scope",
    "research": "project",
    "wiki_stats": "project",
    "wiki_graph": "project",
    "documents": "project",
    "read_file": "project",
    "context": "project",
    "conversations_search": "project",
    "import_conversation": "project",
    "workboard": "project",
    "get_profile": "project_id",
}


class ConflictingArguments(ValueError):
    """Two names for one argument were both given, with different values."""

    def __init__(self, message: str, *, fix: str) -> None:
        super().__init__(message)
        self.fix = fix


def _given(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _project_names_agree(tool: str, arguments: dict[str, Any]) -> None:
    """Raise :class:`ConflictingArguments` when two names give the project different values.

    Checked on the declared names too: ``research`` given ``project`` and
    ``project_id`` used to read ``project_id`` and drop ``project`` silently.
    """
    given = {
        name: arguments[name]
        for name in PROJECT_NAMES
        if name in arguments and _given(arguments[name])
    }
    values = {str(value).strip() for value in given.values()}
    if len(values) > 1:
        target = PROJECT_PARAMETER[tool]
        named = " and ".join(f"`{name}` ({value!r})" for name, value in given.items())
        raise ConflictingArguments(
            f"{tool}: {named} name different projects; nothing was run.",
            fix=f"Pass the project once, as `{target}`: `scope`, `project` and `project_id` "
            "are read as the same argument.",
        )


def _project_aliases(tool: str, declared: set[str]) -> dict[str, str]:
    """The project names ``tool`` does not declare, each mapped to the one that holds it."""
    target = PROJECT_PARAMETER.get(tool)
    if target is None:
        return {}
    return {name: target for name in PROJECT_NAMES if name not in declared and name != target}


def normalize_arguments(
    tool: str, arguments: dict[str, Any] | None, declared: set[str]
) -> dict[str, Any]:
    """``arguments`` with aliases renamed and undeclared Knowledge options folded in."""
    result = dict(arguments or {})
    if tool in PROJECT_PARAMETER:
        _project_names_agree(tool, result)
        for alias, target in _project_aliases(tool, declared).items():
            if alias in result:
                value = result.pop(alias)
                # An empty declared value (``project=""``) is no project: the alias fills it.
                if _given(value) and not _given(result.get(target)):
                    result[target] = value
    for alias, target in ALIASES.get(tool, {}).items():
        if alias in result and alias not in declared:
            value = result.pop(alias)
            if target in result and result[target] != value:
                # Two readings of one argument: refused, not one kept silently.
                raise ConflictingArguments(
                    f"{tool}: `{alias}` and `{target}` are both given, with different "
                    "values; nothing was run.",
                    fix=f"Pass one: this tool takes `{target}`.",
                )
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


def unknown_arguments(
    tool: str, arguments: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any] | None:
    """The ``invalid_arguments`` error for arguments ``tool`` does not take, or None.

    Pydantic ignores an argument the signature does not name, so ``documents``
    given ``scope`` before it was an alias, or ``read_file`` given ``scope``,
    answered for every project the caller can see (Y12). The hosted server's
    input schema says ``additionalProperties: false``; this is the same check,
    after the aliases above are applied.
    """
    properties = set((schema.get("properties") or {}).keys())
    unknown = sorted(set(arguments) - properties)
    if not unknown:
        return None
    named = ", ".join(f"`{key}`" for key in unknown)
    return {
        "status": "error",
        "error_code": "invalid_arguments",
        "message": f"{tool} does not take {named}; nothing was run.",
        "fix": f"Pass only {', '.join(sorted(properties)) or 'no arguments'}: "
        f"{_example(tool, schema)}",
    }


def _example(tool: str, schema: dict[str, Any]) -> str:
    required = list(schema.get("required") or [])
    example = {name: "..." for name in required}
    if tool in FOLDED_OPTIONS:
        example[FOLDED_OPTIONS[tool]] = {"query": "..."} if tool == "knowledge_base" else {}
    return json.dumps(example)


def validation_message(tool: str, error: ValidationError, schema: dict[str, Any]) -> str:
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
    selector = "action" if tool == "knowledge_base_write" else "command"
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
