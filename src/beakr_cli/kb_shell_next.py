"""A reply's structured next steps (``next_calls``) as the ``beakr`` commands that make them.

A reply names its next calls in prose, the way an MCP client makes them (``cat
{page, section_id}``, ``call rows again with offset=3``, ``pass
status=accepted``, ``knowledge_base_write action='add_finding'``), and a
terminal cannot run any of that. :mod:`beakr_cli.kb_shell_hints` translates the
prose it can read; about thirty hint lines of the sandbox run of 2026-11-14
still reached the terminal in MCP wording, because prose does not say which
tool, which arguments or which values.

A reply from a current engine also carries the same steps as data::

    "next_calls": [
        {"tool": "knowledge_base", "command": "cat",
         "arguments": {"page": "<page>", "section_id": "<section_id>"},
         "why": "Read one hit's section."},
        ...
    ]

in the order of the prose ``next``. ``tool`` is the MCP tool, ``command`` its
command (the action, for ``knowledge_base_write``; null for a tool that has
none), ``arguments`` the whole call's arguments (not a difference from the
call that was made), and a value the caller must fill is the string
``"<name>"``. :func:`next_steps` turns each into the ``beakr`` command that
makes the same call, and :func:`next_block` prints them as a ``Next:`` block,
each command followed by its ``why``.

The rules the prose path holds are held here too:

- a command is built only from the entry's structure, through
  :func:`beakr_cli.kb_shell_commands.shell_command` and the same quoting; the
  ``why`` is printed as text and never read for a call;
- an entry with a value a terminal would hide prints no command, only its why;
- an entry no ``beakr`` command can make whole (an unknown tool, an argument
  the command has no option for) prints no command: a command that dropped
  part of the call would read something else;
- a placeholder prints as ``<name>`` and its line says it is a template.

A reply without ``next_calls`` (an older engine), or whose ``next_calls`` is
not a list of such entries, is rendered from its prose as before.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from beakr_cli.kb_shell_commands import (
    DoorCall,
    Slot,
    Unprintable,
    generic_write,
    is_write_action,
    option_word,
    positional_word,
    shell_command,
    shell_quote,
)
from beakr_cli.terminal_text import unprintable

__all__ = ["NextStep", "json_note", "names_a_call", "next_block", "next_steps", "scoped_like"]

#: The door's own commands, which print the door's reply as JSON, not as text.
_JSON_COMMANDS = ("beakr api ",)


def json_note(command: str) -> str:
    """`` (prints JSON)`` after a command that prints JSON, else nothing."""
    return " (prints JSON)" if command.startswith(_JSON_COMMANDS) else ""


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLACEHOLDER = re.compile(r"<([A-Za-z_][A-Za-z0-9_]*)>")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: A word of a printed command that is a placeholder: ``<page>``, ``[<rev>]``.
_SLOT_WORD = re.compile(r"\[?<([A-Za-z_][A-Za-z0-9_]*)>\]?")
#: The names a call gives its project under, on one tool or another.
_PROJECT_KEYS = ("scope", "project", "project_id")
#: The ``beakr kb`` commands that take ``--all`` (every project the caller reads).
_KB_ALL = frozenset({"ls"})


class _NoCommand(ValueError):
    """No ``beakr`` command makes this call whole: only its why is printed."""


@dataclass(frozen=True)
class NextStep:
    """One next step: the command that makes it (None when there is none) and why."""

    command: str | None
    why: str = ""
    #: The ``<placeholders>`` the command leaves for the reader, in order.
    placeholders: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Spec:
    """How one command of a tool is spelled as a ``beakr`` command."""

    path: str
    positional: str | None = None
    required: bool = True
    options: Mapping[str, str] = field(default_factory=dict)
    #: (argument, value) -> the flag that says so; "" when it is the default.
    switches: Mapping[tuple[str, Any], str] = field(default_factory=dict)
    #: Arguments whose value may be a list: the option is repeated.
    repeated: frozenset[str] = frozenset()
    #: Options the command refuses to run without: a placeholder when not given.
    needs: tuple[str, ...] = ()
    #: Arguments whose value is JSON (a list or an object), passed quoted whole.
    json_values: frozenset[str] = frozenset()
    #: Arguments that say nothing this command needs told (a page size it has none of).
    ignored: frozenset[str] = frozenset()


_DOCUMENT_FILTERS = {"path": "--path", "glob": "--glob", "type": "--type"}
_DOCUMENTS: dict[str, _Spec] = {
    "read": _Spec(
        "documents read",
        "document",
        options={"start_line": "--start-line", "end_line": "--end-line"},
    ),
    "grep": _Spec(
        "documents grep",
        "pattern",
        options={
            "context": "--context",
            "output_mode": "--output-mode",
            "head_limit": "--limit",
            "offset": "--offset",
            "document": "--document",
            "after_document": "--after-document",
            **_DOCUMENT_FILTERS,
        },
        switches={
            ("fixed_strings", True): "--fixed-strings",
            ("ignore_case", True): "--ignore-case",
        },
        repeated=frozenset({"glob", "type"}),
    ),
    "ls": _Spec(
        "documents ls",
        "path",
        required=False,
        options={
            "glob": "--glob",
            "type": "--type",
            "limit": "--limit",
            "after_document": "--after-document",
        },
        switches={("recursive", True): "--recursive"},
        repeated=frozenset({"glob", "type"}),
    ),
    "search": _Spec(
        "documents search",
        "query",
        options={
            "limit": "--limit",
            "offset": "--offset",
            "passages": "--passages",
            **_DOCUMENT_FILTERS,
        },
        repeated=frozenset({"glob", "type"}),
    ),
}
#: ``read_file``: the same reader, by the names that tool gives the reference.
_READ_FILE = _Spec(
    "documents read",
    "document",
    options={"start_line": "--start-line", "end_line": "--end-line"},
)
_READ_FILE_REFERENCES = ("document", "artifact_id", "path", "reference")

_CITED = {"evidence": "--evidence", "rationale": "--rationale"}
#: The write actions ``beakr kb`` has a command for. Any other action, or a
#: call with an argument its command has no option for, is the write door's own
#: command (``beakr api knowledge run-wiki-write``), which takes every field.
_WRITES: dict[str, _Spec] = {
    "add_fact": _Spec(
        "kb add-fact",
        "page",
        options={"attribute": "--attribute", "value": "--value", "when": "--when", **_CITED},
        needs=("attribute", "value", "rationale"),
    ),
    "add_connection": _Spec(
        "kb add-connection",
        "page",
        options={"relation": "--relation", "to": "--to", "when": "--when", **_CITED},
        needs=("relation", "to", "rationale"),
    ),
    "add_finding": _Spec(
        "kb add-finding",
        "page",
        options={
            "date": "--date",
            "kind": "--kind",
            "text": "--text",
            "about": "--about",
            "finding_scope": "--finding-scope",
            "supersedes": "--supersedes",
            **_CITED,
        },
        repeated=frozenset({"about"}),
        needs=("date", "kind", "text", "rationale"),
    ),
    "new": _Spec(
        "kb propose create",
        options={
            "title": "--title",
            "sections": "--sections",
            "page_type": "--type",
            "summary": "--summary",
            "rationale": "--rationale",
            "parent": "--parent",
        },
        needs=("title",),
        json_values=frozenset({"sections"}),
    ),
    "edit": _Spec(
        "kb propose edit",
        "page",
        options={"content": "--content", "title": "--title", "summary": "--summary"},
    ),
    "mv": _Spec(
        "kb propose move",
        "page",
        options={"title": "--title", "parent": "--parent", "summary": "--summary"},
    ),
    "archive": _Spec(
        "kb propose archive",
        "page",
        options={"summary": "--summary"},
        switches={("include_children", True): "", ("include_children", False): "--no-children"},
    ),
}

_PROPOSALS = {
    "show_proposal": _Spec(
        "kb proposals show",
        "proposal_id",
        # The tool's `raw` appends the payload as JSON: `--json` prints it all.
        switches={("raw", True): "--json", ("raw", False): ""},
    ),
    "accept_proposal": _Spec(
        "kb proposals accept", "proposal_id", options={"reviewed_hash": "--reviewed-hash"}
    ),
    "dismiss_proposal": _Spec("kb proposals dismiss", "proposal_id"),
}
_RESEARCH = _Spec("research", "query", switches={("quick", False): "--deep", ("quick", True): ""})
_LIST_PROJECTS = _Spec("projects ls", ignored=frozenset({"cursor", "limit"}))
_INTEGRATION_TOOLS = _Spec(
    "integrations tools",
    options={"provider": "--provider", "connector_id": "--connector"},
    ignored=frozenset({"cursor", "limit"}),
)


def _slots(value: Any, found: list[str]) -> Any:
    """``value`` with each ``"<name>"`` string as the placeholder it is."""
    if isinstance(value, str):
        match = _PLACEHOLDER.fullmatch(value.strip())
        if match is None:
            return value
        found.append(match[1])
        return Slot(match[1])
    if isinstance(value, list):
        return [_slots(item, found) for item in value]
    if isinstance(value, dict):
        return {key: _slots(item, found) for key, item in value.items()}
    return value


def _json_slot(value: Any) -> str:
    if isinstance(value, Slot):
        return f"<{value.name}>"
    raise TypeError(type(value).__name__)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _project(arguments: dict[str, Any]) -> Any:
    """The project the call names, taken out of ``arguments`` (None when it names none)."""
    named = [arguments.pop(key) for key in _PROJECT_KEYS if not _empty(arguments.get(key))]
    for key in _PROJECT_KEYS:
        arguments.pop(key, None)
    if len({str(value) for value in named}) > 1:
        raise _NoCommand("the call names two projects")
    return named[0] if named else None


def _spelled(spec: _Spec, arguments: Mapping[str, Any], *, tail: tuple[str, ...] = ()) -> str:
    """``beakr <path> ...`` for ``arguments``; :class:`_NoCommand` when one has no option."""
    given = {key: value for key, value in arguments.items() if not _empty(value)}
    words = [f"beakr {spec.path}"]
    last: list[str] = []
    if spec.positional is not None:
        if spec.positional in given:
            value = given.pop(spec.positional)
            if isinstance(value, (list, dict, bool)):
                raise _NoCommand(spec.positional)
            word = positional_word(value)
            (last if word.startswith("-- ") else words).append(word)
        elif spec.required:
            words.append(str(Slot(spec.positional)))
    for name in spec.needs:
        given.setdefault(name, Slot(name))
    for key, value in given.items():
        if key in spec.ignored:
            continue
        if isinstance(value, bool) or (isinstance(value, str) and (key, value) in spec.switches):
            if (key, value) in spec.switches:
                words.append(spec.switches[(key, value)])
            elif value is not False or (key, True) not in spec.switches:
                # False where the flag only turns it on is the default.
                raise _NoCommand(key)
        elif key not in spec.options:
            raise _NoCommand(key)
        elif key in spec.json_values and not isinstance(value, (str, Slot)):
            body = json.dumps(value, ensure_ascii=False, default=_json_slot)
            words.append(f"{spec.options[key]} {shell_quote(body)}")
        elif isinstance(value, list):
            if key not in spec.repeated or any(isinstance(item, (list, dict)) for item in value):
                raise _NoCommand(key)
            words.extend(option_word(spec.options[key], item) for item in value)
        elif isinstance(value, dict):
            raise _NoCommand(key)
        else:
            words.append(option_word(spec.options[key], value))
    return " ".join(word for word in (*words, *tail, *last) if word)


def scoped_like(
    call: DoorCall | None, command: str, arguments: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """``arguments`` and flags for a ``beakr kb`` command, in the scope ``call`` ran in.

    A step that names no project runs where the command that printed it ran:
    with its project (``--project``), or across every project (``--all``, which
    only ``kb ls`` has). Pasted without it, ``beakr kb ls --parent <id>``
    answered "No project is selected" (sandbox run, 2026-11-14).
    """
    given = dict(arguments)
    if call is None or not _empty(given.get("scope")):
        return given, ()
    everywhere = "--all" in call.flags
    if everywhere:
        return given, ("--all",) if command in _KB_ALL else ()
    scope = call.arguments.get("scope")
    if not _empty(scope):
        given["scope"] = scope
    return given, ()


def _run_scope(call: DoorCall | None) -> Any:
    """The project the command that printed the reply ran in (None: none, or every project)."""
    if call is None or "--all" in call.flags:
        return None
    scope = call.arguments.get("scope")
    return None if _empty(scope) else scope


def _knowledge(command: Any, arguments: dict[str, Any], call: DoorCall | None, tool: str) -> str:
    if not isinstance(command, str) or not _IDENTIFIER.fullmatch(command):
        raise _NoCommand("no command")
    project = _project(arguments)
    if project is not None:
        arguments["scope"] = project
    cursor = dict(call.cursor) if call is not None and call.cursor else {}
    if (
        call is not None
        and tool == "knowledge_base"
        and cursor
        and command == call.command
        and all(key in arguments and arguments[key] == value for key, value in cursor.items())
    ):
        # The reply's own continuation: the command that was run, with the
        # cursor from the reply's coverage and the flags the door never saw.
        return shell_command(call.command, {**call.arguments, **cursor}, flags=call.flags)
    given, flags = scoped_like(call, command, arguments)
    return shell_command(command, given, flags=flags)


def _write(command: Any, arguments: dict[str, Any], call: DoorCall | None) -> str:
    action = command if isinstance(command, str) and command else arguments.pop("action", None)
    arguments.pop("action", None)
    if not isinstance(action, str) or not is_write_action(action):
        raise _NoCommand("not a write action")
    project = _project(arguments)
    if project is None:
        project = _run_scope(call)
    spec = _WRITES.get(action)
    if spec is not None:
        tail = () if project is None else (option_word("--project", project),)
        try:
            return _spelled(spec, arguments, tail=tail)
        except _NoCommand:
            pass
    if project is not None:
        arguments["scope"] = project
    return generic_write(action, arguments)


def _documents(command: Any, arguments: dict[str, Any], call: DoorCall | None, tool: str) -> str:
    spec = _DOCUMENTS.get(command) if isinstance(command, str) else None
    if spec is None:
        raise _NoCommand("no such documents command")
    return _spelled(spec, arguments, tail=_document_scope(arguments, call, tool))


def _document_scope(arguments: dict[str, Any], call: DoorCall | None, tool: str) -> tuple[str, ...]:
    """``--project`` or ``--all`` for a documents command: the call's own, else the run's."""
    project = _project(arguments)
    if project is not None:
        return (option_word("--project", project),)
    if call is not None and "--all" in call.flags and tool == "documents":
        # `--all` of a documents command: every documents command has it.
        return ("--all",)
    scope = _run_scope(call)
    return () if scope is None else (option_word("--project", scope),)


def _read_file(command: Any, arguments: dict[str, Any], call: DoorCall | None, tool: str) -> str:
    named = [key for key in _READ_FILE_REFERENCES if not _empty(arguments.get(key))]
    if len(named) > 1:
        raise _NoCommand("the call names the file twice")
    if named:
        arguments["document"] = arguments.pop(named[0])
    # `offset` and `limit` are a window in characters; `beakr documents read`
    # reads by line and has no option for them: no command drops them.
    return _spelled(_READ_FILE, arguments, tail=_document_scope(arguments, call, tool))


def _integrations(command: Any, arguments: dict[str, Any]) -> str:
    if command == "list":
        return _spelled(_INTEGRATION_TOOLS, arguments)
    if command != "call":
        raise _NoCommand("no beakr command for it")
    tool = arguments.pop("tool", None)
    sent = arguments.pop("arguments", None)
    connector = arguments.pop("connector_id", None)
    if any(not _empty(value) for value in arguments.values()):
        raise _NoCommand("an argument with no option")
    if isinstance(tool, Slot):
        name = str(tool)
    elif isinstance(tool, str) and _IDENTIFIER.fullmatch(tool):
        name = tool
    else:
        raise _NoCommand("no tool")
    words = [f"beakr integrations call {name}"]
    if isinstance(sent, Slot):
        words.append(f"--args {sent}")
    elif isinstance(sent, Mapping) and sent:
        if not all(isinstance(key, str) and _IDENTIFIER.fullmatch(key) for key in sent):
            raise _NoCommand("an argument name that is not one")
        body = json.dumps(dict(sent), ensure_ascii=False, default=_json_slot)
        words.append(f"--args {shell_quote(body)}")
    elif not _empty(sent):
        raise _NoCommand("arguments that are not an object")
    if not _empty(connector):
        words.append(option_word("--connector", connector))
    return " ".join(words)


def _research(arguments: dict[str, Any], call: DoorCall | None, tool: str) -> str:
    project = _project(arguments)
    if project is None:
        project = _run_scope(call)
    tail = () if project is None else (option_word("--project", project),)
    return _spelled(_RESEARCH, arguments, tail=tail)


def _proposal(tool: str, arguments: dict[str, Any], call: DoorCall | None) -> str:
    staged = call.proposal_id if call is not None else None
    target = arguments.get("proposal_id")
    if _empty(target) and staged:
        # The proposal this write staged is the one its reply means.
        arguments["proposal_id"] = target = staged
    if not _empty(target) and not isinstance(target, Slot):
        # Printed bare after the verb: an id, and nothing an option parser or a
        # shell could read another way.
        if not isinstance(target, str) or not _UUID.fullmatch(target):
            raise _NoCommand("not a proposal id")
    return _spelled(_PROPOSALS[tool], arguments)


def _build(entry: Mapping[str, Any], call: DoorCall | None, ran: str, found: list[str]) -> str:
    tool, command = entry.get("tool"), entry.get("command")
    raw = entry.get("arguments")
    if raw is not None and not isinstance(raw, Mapping):
        raise _NoCommand("arguments that are not an object")
    if unprintable(tool) or unprintable(command) or unprintable(dict(raw or {})):
        raise Unprintable(str(tool))
    arguments = {
        str(key): _slots(value, found) for key, value in (raw or {}).items() if value is not None
    }
    if tool == "knowledge_base":
        return _knowledge(command, arguments, call, ran)
    if tool == "knowledge_base_write":
        return _write(command, arguments, call)
    if tool == "documents":
        return _documents(command, arguments, call, ran)
    if tool == "read_file":
        return _read_file(command, arguments, call, ran)
    if tool == "integrations":
        return _integrations(command, arguments)
    if tool == "research":
        return _research(arguments, call, ran)
    if tool in _PROPOSALS:
        return _proposal(str(tool), arguments, call)
    if tool == "list_projects":
        return _spelled(_LIST_PROJECTS, arguments)
    raise _NoCommand("an unknown tool")


def _placeholders(command: str) -> tuple[str, ...]:
    """The ``<placeholders>`` left as words of ``command``, in order, each once."""
    try:
        words = shlex.split(command)
    except ValueError:
        return ()
    names: list[str] = []
    for word in words:
        match = _SLOT_WORD.fullmatch(word)
        if match is not None and match[1] not in names:
            names.append(match[1])
    return tuple(names)


def next_steps(
    next_calls: Any, call: DoorCall | None = None, *, tool: str = "knowledge_base"
) -> list[NextStep] | None:
    """Each entry of a reply's ``next_calls`` as a step, or None when the reply has none.

    ``call`` is the call that was made and ``tool`` the MCP tool it belongs to
    (``knowledge_base``, ``documents``, ``research``): a step of that tool that
    names no project is given the project (or ``--all``) the call ran with.
    None means "render the prose": ``next_calls`` is absent, empty, or not a
    list of ``{tool, command, arguments, why}`` objects.
    """
    if not isinstance(next_calls, list) or not next_calls:
        return None
    steps: list[NextStep] = []
    for entry in next_calls:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("tool"), str):
            return None
        why = entry.get("why")
        why = " ".join(why.split()) if isinstance(why, str) else ""
        found: list[str] = []
        try:
            command: str | None = _build(entry, call, tool, found)
        except (_NoCommand, Unprintable):
            command = None
        placeholders = _placeholders(command) if command else ()
        if command and found and not placeholders:
            # A placeholder inside a quoted JSON body: still one to fill in.
            placeholders = tuple(dict.fromkeys(found))
        steps.append(NextStep(command, why, placeholders))
    return steps


def next_block(steps: list[NextStep]) -> list[str]:
    """The ``Next:`` block: each command on its own line, its why under it.

    A command with a placeholder is marked as a template; a step with no
    command prints its why alone.
    """
    lines: list[str] = []
    for step in steps:
        if step.command is None:
            if step.why:
                lines.append(f"  - {step.why}")
            continue
        notes = []
        if step.placeholders:
            notes.append("template: replace " + ", ".join(f"<{n}>" for n in step.placeholders))
        if json_note(step.command):
            notes.append("prints JSON")
        lines.append(f"  {step.command}" + (f"    ({'; '.join(notes)})" if notes else ""))
        if step.why:
            lines.append(f"      {step.why}")
    return ["Next:", *lines] if lines else []


# ---------------------------------------------------------------------------
# Which prose steps the block replaces
# ---------------------------------------------------------------------------

#: A prose ``next`` line that names a call, in the wording an MCP client reads:
#: a tool, ``key=value``, a backticked name, ``command {arguments}``.
_NAMES_A_CALL = re.compile(
    r"\b(?:knowledge_base(?:_write)?|read_file|integrations|accept_proposal|show_proposal|"
    r"dismiss_proposal|list_projects|upload_files)\b"
    r"|\b[a-z][a-z0-9_]*=\S"
    r"|`[^`\n]+`"
    r"|\{[^{}\n]*\}"
)


def names_a_call(hint: str, translated: Callable[[str], str] | None = None) -> bool:
    """Whether a prose ``next`` line names a call, so ``next_calls`` stands for it.

    ``translated`` is the prose translator: a line it changes named a call.
    A line that names none (how many records were left out, how to cite) says
    something the structured steps do not, and is still printed.
    """
    if _NAMES_A_CALL.search(hint):
        return True
    return translated is not None and translated(hint) != hint
