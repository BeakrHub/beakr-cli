"""A Knowledge door call as the ``beakr`` command that makes it.

:func:`shell_command` turns one door call (command and arguments) into the
``beakr kb ...`` invocation for every command and option the CLI has, and into
the generic door command (``beakr api knowledge run-wiki-command``, which takes
every argument of every door command) for anything ``beakr kb`` has no flag
for. A hint built from it therefore always runs, and never drops part of the
call. :mod:`beakr_cli.kb_shell_hints` finds the calls in a reply's text.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "COMMANDS",
    "DOOR_COMMANDS",
    "GENERIC_WRITE",
    "DoorCall",
    "Slot",
    "shell_command",
    "shell_word",
]


@dataclass(frozen=True)
class Slot:
    """An argument a hint names without a value: ``{page}``, or ``{rev2?}`` when optional."""

    name: str
    optional: bool = False

    def __str__(self) -> str:
        return f"<{self.name}>"


@dataclass(frozen=True)
class DoorCall:
    """The door call a command made, so a hint that says "again with offset=50" can be whole."""

    command: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    #: The proposal a write staged: what ``accept_proposal`` in its reply refers to.
    proposal_id: str | None = None
    #: Flags the command was run with that the door call does not carry (``--all``
    #: reads every project by sending no scope): a continuation must repeat them.
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Spec:
    """How one door command is spelled as a ``beakr`` command."""

    path: str
    #: The door argument the CLI takes as its positional argument, if any.
    positional: str | None = None
    #: Whether the CLI refuses the command without that positional.
    required: bool = True
    #: Door argument -> the flag that takes its value.
    options: Mapping[str, str] = field(default_factory=dict)
    #: (door argument, value) -> the flag that says so; "" when it is the default.
    switches: Mapping[tuple[str, Any], str] = field(default_factory=dict)
    #: An extra condition the arguments must meet for this spelling to be right.
    fits: Callable[[Mapping[str, Any]], bool] = lambda arguments: True


_AUTHOR = {"by": "--by", "via": "--via", "kind": "--kind", "accepted_by": "--accepted-by"}
_WINDOW = {"since": "--since", "until": "--until", "offset": "--offset"}
_PAGE_AUTHOR = {
    f"{verb}_{what}": f"--{verb}-{what}"
    for verb in ("created", "changed")
    for what in ("by", "via", "since")
}

#: Every door command ``beakr kb`` has a command for, with the door arguments
#: that command has a flag for. A call with any other argument is spelled as the
#: generic door command instead: a hint must never drop part of the call.
COMMANDS: dict[str, _Spec] = {
    "cat": _Spec(
        "kb cat",
        "page",
        options={
            "rev": "--rev",
            "section_id": "--section-id",
            "lines": "--lines",
            "source_offset": "--source-offset",
        },
        switches={("outline", True): "--outline", ("detail", "full"): "--full"},
        # --source-offset pages the records of a --full read.
        fits=lambda a: "source_offset" not in a or a.get("detail") == "full",
    ),
    "sections": _Spec("kb sections", "query", options={"page_type": "--type", "limit": "--limit"}),
    "grep": _Spec(
        "kb grep",
        "query",
        options={
            "context_lines": "--context",
            "limit": "--limit",
            "after_title": "--after-title",
            "after_page_id": "--after-page-id",
        },
        switches={("mode", "regex"): "--regex", ("mode", "text"): ""},
    ),
    "ls": _Spec(
        "kb ls",
        options={
            "page_type": "--type",
            "parent": "--parent",
            "limit": "--limit",
            "after_title": "--after-title",
            **_PAGE_AUTHOR,
        },
        # `kb ls --after-title` continues only a listing filtered by author.
        fits=lambda a: "after_title" not in a or any(key in a for key in _PAGE_AUTHOR),
    ),
    "sources": _Spec("kb sources", "page"),
    "hover": _Spec("kb hover", "page"),
    "blame": _Spec("kb blame", "page"),
    "provenance": _Spec(
        "kb provenance", "page", options={"limit": "--limit", "after_section": "--after-section"}
    ),
    "links": _Spec(
        "kb links",
        "page",
        options={"direction": "--direction", "limit": "--limit", "offset": "--offset"},
    ),
    "timeline": _Spec(
        "kb timeline",
        "page",
        required=False,
        options={
            "timeline_query": "--query",
            "start_date": "--start",
            "end_date": "--end",
            "page_type": "--type",
            "limit": "--limit",
            "cursor": "--cursor",
        },
        switches={("include_content", True): "--content"},
    ),
    "log": _Spec("kb log", "page", required=False, options={**_AUTHOR, **_WINDOW}),
    "proposals": _Spec(
        "kb proposals ls",
        options={"status": "--status", "limit": "--limit", **_AUTHOR, **_WINDOW},
    ),
    "rows": _Spec(
        "kb rows",
        options={
            "table": "--table",
            "entity": "--entity",
            "attribute": "--attribute",
            "match": "--match",
            "view": "--view",
            "query": "--query",
            "start_date": "--start",
            "end_date": "--end",
            "as_of": "--as-of",
            "to": "--to",
            "connected_to": "--connected-to",
            "group_by": "--group-by",
            "order_by": "--order-by",
            "page_type": "--type",
            "limit": "--limit",
            "offset": "--offset",
            "after_card": "--after-card",
        },
        switches={
            ("current_only", True): "--current-only",
            ("descending", True): "--desc",
            ("include_evidence", True): "--evidence",
        },
    ),
    "query": _Spec("kb query", "sql", options={"cursor": "--cursor"}),
}

#: Every command of the door, for finding a call in a hint.
DOOR_COMMANDS = (
    *COMMANDS,
    "diff",
    "show",
    "completions",
    "references",
    "ontology",
    "diagnostics",
    "suggest_parent",
)
#: The argument each door command refuses to run without (its ``Usage:`` line).
_REQUIRED = {
    # Not `query`: it takes its SQL or a JSON `spec`.
    **{
        name: spec.positional
        for name, spec in COMMANDS.items()
        if spec.positional and spec.required and name != "query"
    },
    "diff": "page",
    "show": "page",
    "references": "page",
    "completions": "prefix",
}
_GENERIC = "beakr api knowledge run-wiki-command"
GENERIC_WRITE = "beakr api knowledge run-wiki-write"


def shell_command(command: str, arguments: Mapping[str, Any] | None = None) -> str:
    """The ``beakr`` command that makes the door call ``command`` with ``arguments``.

    ``scope`` is the project (``--project``). A value may be a :class:`Slot`
    for an argument the hint names without a value; it prints as ``<name>``.
    """
    given = {key: value for key, value in (arguments or {}).items() if value is not None}
    spec = COMMANDS.get(command)
    if spec is None or not spec.fits(given):
        return _generic(command, given)
    words = [f"beakr {spec.path}"]
    if spec.positional is not None:
        if spec.positional in given:
            words.append(_positional(given.pop(spec.positional)))
        elif spec.required:
            words.append(str(Slot(spec.positional)))
    for key, value in given.items():
        if key == "scope":
            words.append(_option("--project", value))
        elif key in spec.options:
            words.append(_option(spec.options[key], value))
        elif isinstance(value, bool) and not value and (key, True) in spec.switches:
            continue
        elif isinstance(value, (str, bool)) and (key, value) in spec.switches:
            words.append(spec.switches[(key, value)])
        else:
            return _generic(command, {key: value for key, value in (arguments or {}).items()})
    return " ".join(word for word in words if word)


def _generic(command: str, arguments: Mapping[str, Any]) -> str:
    """The door's own command, which takes every argument of every door command."""
    given = {key: value for key, value in arguments.items() if value is not None}
    needed = _REQUIRED.get(command)
    if needed is not None and needed not in given:
        given = {needed: Slot(needed), **given}
    if any(isinstance(value, (dict, list)) for value in given.values()):
        body = json.dumps({"command": command, **given}, ensure_ascii=False, default=str)
        return f"{_GENERIC} --data {shlex.quote(body)}"
    words = [f"{_GENERIC} --command {command}"]
    for key, value in given.items():
        flag = "--project" if key == "scope" else "--" + key.replace("_", "-")
        if isinstance(value, bool):
            words.append(flag if value else "--no-" + flag.removeprefix("--"))
        else:
            words.append(_option(flag, value))
    return " ".join(words)


def shell_word(value: Any) -> str:
    return str(value) if isinstance(value, Slot) else shlex.quote(str(value))


def _positional(value: Any) -> str:
    if isinstance(value, Slot):
        return f"[{value}]" if value.optional else str(value)
    text = str(value)
    # A value that opens with a dash would be read as an option.
    return f"-- {shlex.quote(text)}" if text.startswith("-") else shlex.quote(text)


def _option(flag: str, value: Any) -> str:
    text = f"{flag} {shell_word(value)}"
    return f"[{text}]" if isinstance(value, Slot) and value.optional else text
