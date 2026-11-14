"""A Knowledge door call as the ``beakr`` command that makes it.

:func:`shell_command` turns one door call (command and arguments) into the
``beakr kb ...`` invocation for every command and option the CLI has, and into
the generic door command (``beakr api knowledge run-wiki-command``) for
anything ``beakr kb`` has no flag for: as that command's options where it has
one for every argument, and otherwise as its ``--data '<json>'`` body, which
carries any call. A hint built from it therefore always runs, and never drops
part of the call. Every value is quoted for the shell, and one that opens with
a dash is bound to its flag (``--after-title=-x``) or follows ``--``.
:mod:`beakr_cli.kb_shell_hints` finds the calls in a reply's text.

A value is what someone else wrote (a page title, a cursor made of one), and
the printed command is pasted into a shell, so:

- a word prints bare only when every character of it is on a short list that
  no shell reads specially (:func:`shell_quote`); the standard library's
  quoting leaves ``=`` bare, and zsh runs ``=ls`` as ``/bin/ls``;
- a value holding a control, bidirectional or zero-width character is not
  printed at all (:class:`Unprintable`): the line a terminal shows for it would
  not be the command it runs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from beakr_cli.terminal_text import unprintable

__all__ = [
    "COMMANDS",
    "DOOR_COMMANDS",
    "GENERIC_ONLY",
    "GENERIC_WRITE",
    "DoorCall",
    "Slot",
    "Unprintable",
    "is_write_action",
    "shell_command",
    "shell_quote",
    "shell_word",
]


#: An argument or command name that is safe to print unquoted in a shell command.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: The characters a word may print bare with: none of them is special to bash
#: or zsh, at the start of a word or inside it. Not ``=`` (zsh expands ``=ls``
#: to the path of ``ls``), not ``~``, no opening dash, nothing that globs,
#: expands or separates.
_BARE_WORD = re.compile(r"[A-Za-z0-9_./:@%+,][A-Za-z0-9_./:@%+,-]*")


class Unprintable(ValueError):
    """A value that cannot be printed in a command: it holds a character a terminal hides."""


def shell_quote(text: str) -> str:
    """``text`` as one shell word: bare when it is plainly safe, else in single quotes."""
    if _BARE_WORD.fullmatch(text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


@dataclass(frozen=True)
class Slot:
    """An argument a hint names without a value: ``{page}``, or ``{rev2?}`` when optional."""

    name: str
    optional: bool = False

    def __str__(self) -> str:
        # The name comes from a reply's text. Only a plain identifier prints bare;
        # anything else is quoted, so a pasted command can never run what a reply
        # put inside the placeholder.
        shown = f"<{self.name}>"
        if unprintable(shown):
            raise Unprintable(shown)
        return shown if _IDENTIFIER.fullmatch(self.name) else shell_quote(shown)


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
    #: The arguments that continue a cut reply, from its ``coverage``
    #: (``continue_with``, else ``next_offset`` or ``next_cursor``). A
    #: continuation is built from these and the call itself, not from the
    #: reply's prose, which cannot carry every title (one with a double quote).
    cursor: Mapping[str, Any] | None = None


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
            "after_page_id": "--after-page-id",
            **_PAGE_AUTHOR,
        },
        # `kb ls --after-title` continues only a listing filtered by author.
        fits=lambda a: (
            not ({"after_title", "after_page_id"} & set(a)) or any(key in a for key in _PAGE_AUTHOR)
        ),
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

#: The arguments the door takes that ``beakr kb`` has no flag for, by command:
#: a call carrying one is spelled as the generic door command. Listed so that a
#: new door argument is a decision here, not a hint that silently stops running
#: (``kb ls`` printed ``--after-page-id`` on the generic command, which has no
#: such option: live check 2026-11-13). ``tests/test_cli_kb_shell_commands.py``
#: holds every argument the engine accepts to this table or to ``COMMANDS``.
GENERIC_ONLY: dict[str, frozenset[str]] = {
    "ls": frozenset({"sort_by"}),
    "grep": frozenset({"page_type", "output_mode", "target", "matches_per_page", "detail"}),
    "sections": frozenset({"detail", "include_sources"}),
    "log": frozenset({"limit"}),
    "sources": frozenset({"limit", "after_title", "after_source_id"}),
    "provenance": frozenset({"sources_per_section"}),
    "links": frozenset({"relation", "page_type"}),
    "timeline": frozenset({"include_approx", "include_children"}),
    "rows": frozenset({"relation", "kind", "field_id", "regex"}),
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


@cache
def _write_actions() -> frozenset[str]:
    from beakr_cli.commands.api import operations

    for entry in operations():
        if entry["area"] == "knowledge" and entry["action"] == "run-wiki-write":
            for body_field in entry["body"]["fields"]:
                if body_field["name"] == "action":
                    return frozenset(body_field.get("enum") or ())
    return frozenset()


def is_write_action(name: str) -> bool:
    """Whether the write door has the action ``name`` (the vendored API contract's list)."""
    return name in _write_actions()


def shell_command(
    command: str, arguments: Mapping[str, Any] | None = None, *, flags: tuple[str, ...] = ()
) -> str:
    """The ``beakr`` command that makes the door call ``command`` with ``arguments``.

    ``scope`` is the project (``--project``). A value may be a :class:`Slot`
    for an argument the hint names without a value; it prints as ``<name>``.
    ``flags`` are options of the ``beakr kb`` command the door call does not
    carry (:attr:`DoorCall.flags`); the generic command has none of them.

    Raises :class:`Unprintable` when the command or any name or value holds a
    control, bidirectional or zero-width character: no command is printed for it.
    """
    given = {key: value for key, value in (arguments or {}).items() if value is not None}
    if unprintable(command) or unprintable(given):
        raise Unprintable(command)
    spec = COMMANDS.get(command)
    if spec is None or not spec.fits(given):
        return _generic(command, given)
    words = [f"beakr {spec.path}"]
    # A positional that opens with a dash goes last, after `--`: everything
    # after `--` is positional, so no option may follow it.
    last: list[str] = []
    if spec.positional is not None:
        if spec.positional in given:
            word = _positional(given.pop(spec.positional))
            (last if word.startswith("-- ") else words).append(word)
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
            return _generic(command, dict(arguments or {}))
    return " ".join(word for word in (*words, *flags, *last) if word)


def _generic_fields() -> dict[str, dict[str, Any]]:
    """The options ``beakr api knowledge run-wiki-command`` has, by the door argument each sets."""
    from beakr_cli.commands.api import operations

    for entry in operations():
        if entry["area"] == "knowledge" and entry["action"] == "run-wiki-command":
            return {field["name"]: field for field in entry["body"]["fields"]}
    return {}


def _takes(field: dict[str, Any] | None, value: Any) -> bool:
    """Whether the generic command's option for ``field`` can carry ``value``."""
    if field is None:
        return False
    kind = field["type"]
    if isinstance(value, Slot):
        return kind in ("string", "integer")
    if kind == "boolean":
        return isinstance(value, bool)
    if isinstance(value, (bool, dict, list)):
        return False
    if field.get("enum"):
        return value in field["enum"]
    if kind == "integer":
        return isinstance(value, int)
    return kind == "string"


def _generic(command: str, arguments: Mapping[str, Any]) -> str:
    """The door's own command, which takes every argument of every door command.

    As options where the command has one for every argument of the call. It is
    generated from the API contract this CLI was built with, so an argument the
    door has gained since (``after_page_id``), a value outside an option's
    choices, or a name that is not an identifier has no option: the whole call
    then goes in ``--data`` as one quoted JSON body, which the command sends
    as it is. Either way the printed command parses and nothing is dropped.
    """
    # `command` is the door's own field, never an argument of a call: one in a
    # hint's arguments would replace the command this line names.
    given = {
        key: value for key, value in arguments.items() if value is not None and key != "command"
    }
    needed = _REQUIRED.get(command)
    if needed is not None and needed not in given:
        given = {needed: Slot(needed), **given}
    fields = _generic_fields()
    if not _takes(fields.get("command"), command) or not all(
        # Argument and command names come from a reply's text: only a name the
        # command really has an option for becomes a flag.
        _IDENTIFIER.fullmatch(str(key)) and _takes(fields.get(key), value)
        for key, value in given.items()
    ):
        body = json.dumps({"command": command, **given}, ensure_ascii=False, default=_json_slot)
        return f"{_GENERIC} --data {shell_quote(body)}"
    words = [f"{_GENERIC} --command {command}"]
    for key, value in given.items():
        flag = "--project" if key == "scope" else "--" + key.replace("_", "-")
        if isinstance(value, bool):
            words.append(flag if value else "--no-" + flag.removeprefix("--"))
        else:
            words.append(_option(flag, value))
    return " ".join(words)


def _json_slot(value: Any) -> str:
    # In a JSON body a placeholder is a string value; the body is quoted whole.
    return f"<{value.name}>" if isinstance(value, Slot) else str(value)


def shell_word(value: Any) -> str:
    if isinstance(value, Slot):
        return str(value)
    text = str(value)
    if unprintable(text):
        raise Unprintable(text)
    return shell_quote(text)


def _positional(value: Any) -> str:
    if isinstance(value, Slot):
        return f"[{value}]" if value.optional else str(value)
    text = str(value)
    # A value that opens with a dash would be read as an option.
    return f"-- {shell_word(text)}" if text.startswith("-") else shell_word(text)


def _option(flag: str, value: Any) -> str:
    # A value that opens with a dash would be read as another option: joined
    # to its flag with `=`, it can only be the flag's value.
    joiner = "=" if not isinstance(value, Slot) and str(value).startswith("-") else " "
    text = f"{flag}{joiner}{shell_word(value)}"
    return f"[{text}]" if isinstance(value, Slot) and value.optional else text
