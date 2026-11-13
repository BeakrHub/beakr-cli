"""Knowledge door calls as the ``beakr`` commands a person can paste into a shell.

The Knowledge door answers every caller in one voice: its hints name the next
call the way an MCP client makes it (``cat {page, section_id}``, ``proposals
{"status": "accepted", "offset": 1}``, ``call accept_proposal``, ``read_file
<id>``). The stdio MCP server passes those through unchanged, because its
agents do call them that way. ``beakr kb ...`` printed them unchanged too, and
none of them runs in a terminal (live check 2026-11-13: ``kb cat`` printed
``cat {page: "...", lines: "5-533"}`` where ``beakr kb cat <id> --lines 5-533``
is the command).

Two layers:

- :func:`beakr_cli.kb_shell_commands.shell_command` turns one door call
  (command and arguments) into the ``beakr kb ...`` invocation that makes it,
  for every command and option the CLI has, and into the generic door command
  (``beakr api knowledge run-wiki-command``) for anything it has no flag for,
  so the result always runs.
- :func:`shell_reply` renders a door reply for a terminal: it finds the calls a
  reply's hints name and replaces each with its command. ``--json`` output
  never passes through here.

What is rewritten is bounded on purpose. A reply's ``content`` also quotes the
organization's own pages and files, which may themselves mention ``cat {page}``
or ``accept_proposal``; rewriting those would misquote them. So calls are
translated in the ``next`` hints, in the ``Approval state`` line, in a write's
acknowledgement, and in the opening and closing paragraphs of a listing, where
the door puts its own notices. A page body (``cat``) is never touched. Sources
lines get only the two exact forms they carry (``read_file <id>`` and the
``integrations`` read call).

A continuation ("call ls again with after_title=...") is built from the call
``beakr kb`` made and the reply's structured ``coverage`` whenever the reply
carries one (:func:`with_cursor`); the prose only says where the command goes.
Prose alone cannot carry every value: the door printed a title holding a double
quote unescaped, and the hint built from it stopped at that quote and listed
the same page again (live check 2026-11-13). Without a structured continuation
a value is read from prose only when it reads one way, as a JSON string or a
plain token, and otherwise the hint is left as the door wrote it: no command is
better than a wrong one.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from beakr_cli.kb_shell_commands import (
    COMMANDS,
    DOOR_COMMANDS,
    GENERIC_WRITE,
    DoorCall,
    Slot,
    shell_command,
    shell_word,
)

__all__ = [
    "DoorCall",
    "Slot",
    "shell_command",
    "shell_content",
    "shell_hint",
    "shell_reply",
    "with_cursor",
]


# ---------------------------------------------------------------------------
# Finding the calls a hint names
# ---------------------------------------------------------------------------

_NAMES = "|".join(sorted(DOOR_COMMANDS, key=len, reverse=True))
_BRACES = r"\{[^{}\n]*\}"
#: ``cat {page, section_id}``, then any ``or {page, direction: out}`` for the same command.
_BRACE_CALL = re.compile(
    rf"(?<![\w`/.-])(?P<command>{_NAMES}) (?P<first>{_BRACES})"
    rf"(?P<more>(?:(?:,| or| and) {_BRACES})*)"
)
_MORE = re.compile(rf"(?P<joiner>,| or| and) (?P<body>{_BRACES})")
#: `` `cat <page> section_id=<id>` ``: the same call in the door's other spelling.
_TICKED_CALL = re.compile(rf"`(?P<command>{_NAMES})(?P<rest>(?: [^`\n]+)?)`")
_ITEM = re.compile(r"(?P<name>[A-Za-z_]\w*)(?P<optional>\?)?(?:\s*:\s*(?P<value>.+))?")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

#: Arguments a reply names to continue a cut read (``offset=50``, ``after_title="M"``).
_CONTINUATION_KEYS = (
    "offset",
    "after_title",
    "after_page_id",
    "after_source_id",
    "after_section",
    "after_card",
    "cursor",
    "source_offset",
    "section_id",
    "lines",
)
_KEY = "|".join(_CONTINUATION_KEYS)
#: A value pinned from the reply's structured coverage: ``\x01<index>\x01``.
_PINNED = r"\x01\d+\x01"
#: A value written without quotes: an id, a number, a line range, a cursor.
#: Anything else unquoted (``after_title='Kickoff Notes'``, Python's repr) has
#: no certain end and is not read as a value.
_PLAIN = r"[A-Za-z0-9_](?:[A-Za-z0-9_.:/+=@~%-]*[A-Za-z0-9_/+=@~%-])?"
_END = r"(?=$|[\s,;:)`]|\.(?:\s|$))"
#: Pinned, a JSON string, a ``<placeholder>``, or a plain token; each must end
#: where a value can end, so a quote in the middle of a title is not its end.
_VALUE = rf'{_PINNED}|"(?:[^"\\\n]|\\.)*"{_END}|<[^<>\n]*>|{_PLAIN}{_END}'
_PAIR = rf"(?<![\w-])(?:{_KEY})=(?:{_VALUE})"
_CONTINUATION = re.compile(
    rf"(?P<lead>(?:[Cc]all )?(?P<command>{_NAMES}) (?:again )?with |re-run with |"
    r"[Cc]ontinue with )?"
    r"(?:the same (?:filters|arguments) and )?"
    rf"(?P<pairs>{_PAIR}(?:,? (?:and )?{_PAIR})*)"
    r"(?: and the same filters)?"
)
_UNREAD_PAIR = re.compile(rf"(?<![\w-])(?:{_KEY})=[\"']")
_PAIR_PARTS = re.compile(rf"(?<![\w-])(?P<key>{_KEY})=(?P<value>{_VALUE})")
#: The argument each command's ``coverage.next_cursor`` is sent back as (the
#: engine's ``kb_result_coverage._CURSOR_ARGUMENT``).
_CURSOR_ARGUMENT = {"rows": "after_card"}

_INTEGRATION_CALL = re.compile(
    r"integrations command='call' tool='(?P<tool>\w+)' arguments=(?P<arguments>\{[^{}\n]*\})"
)
#: The door's older connector line: ``fetch: granola_get_note with note_id=not_1``.
_FETCH_LINE = re.compile(r"^(?P<lead>\s*fetch: )(?P<tool>[a-z][a-z0-9_]*) with (?P<pairs>\w+=.+)$")
_READ_LINE = re.compile(rf"^(?P<lead>\s*read: )read_file with artifact_id=(?P<id>{_UUID})$")
_PROPOSAL_TOOL = re.compile(r"(?P<call>[Cc]all )?\b(?P<verb>show|accept|dismiss)_proposal\b")
_KNOWLEDGE_TOOL = re.compile(rf"\bknowledge_base command='?(?P<command>{_NAMES})'?")
_WRITE_TOOL = re.compile(r"\bknowledge_base_write action='(?P<action>\w+)'")
_BARE_COMMAND = re.compile(r"(?<=\bwith )(?P<command>cat|show)\b(?! [{<`])")
#: What a bare ``show`` may also take (the door's ``Usage: show <page> [--rev N]``).
_BARE_ARGUMENTS = {"show": {"page": Slot("page"), "rev": Slot("rev", optional=True)}}
_DOCUMENTS = re.compile(r"(?<!beakr )\bdocuments (?P<verb>read|grep)\b(?: \(read_file in MCP\))?")
#: The documents commands a hint names, with what each one needs.
_DOCUMENT_COMMANDS = {
    "read": "beakr documents read <document>",
    "grep": "beakr documents grep <pattern>",
}
_BRIEF_NOTE = re.compile(r"sources='full' adds each document's excerpt, url and read_file id")


class _Unreadable(ValueError):
    """A value in a hint that does not read one way: the hint stays as the door wrote it."""


def _literal(text: str) -> Any:
    text = text.strip()
    if text.startswith('"'):
        # A JSON string, escapes included. One that does not parse (a title's
        # own quote or backslash, printed unescaped) has no certain value.
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise _Unreadable(text) from exc
        # `\n`, `\t` and the like are also what a raw `C:\new` looks like: only
        # the escapes a title needs in JSON (quote, backslash, non-ASCII) are read.
        if not isinstance(value, str) or any(
            escape not in '"\\/u' for escape in re.findall(r"\\(.)", text)
        ):
            raise _Unreadable(text)
        return value
    if text.startswith("<") and text.endswith(">"):
        return Slot(text[1:-1])
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return {"true": True, "false": False}.get(text, text)


def _split(inner: str) -> list[str]:
    """``inner`` at its commas, leaving the ones inside double quotes."""
    items, current, quoted, escaped = [], "", False, False
    for char in inner:
        if char == '"' and not escaped:
            quoted = not quoted
        # A backslash escapes the next character only; `\\"` ends the string.
        escaped = quoted and char == "\\" and not escaped
        if char == "," and not quoted:
            items.append(current)
            current = ""
        else:
            current += char
    return [item for item in (*items, current) if item.strip()]


def _brace_arguments(body: str) -> dict[str, Any] | None:
    """``{page, section_id?}``, ``{page: "x", lines: "5-9"}`` or a JSON object, as arguments."""
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    arguments: dict[str, Any] = {}
    for item in _split(body[1:-1]):
        match = _ITEM.fullmatch(item.strip())
        if match is None:
            return None
        name = match["name"]
        try:
            arguments[name] = (
                Slot(name, optional=bool(match["optional"]))
                if match["value"] is None
                else _literal(match["value"])
            )
        except _Unreadable:
            return None
    return arguments


def _ticked_arguments(command: str, rest: str) -> dict[str, Any] | None:
    """``<page> section_id=<id>`` as arguments: a bare ``<name>`` is the positional."""
    spec = COMMANDS.get(command)
    positional = spec.positional if spec is not None and spec.positional else "page"
    arguments: dict[str, Any] = {}
    for token in rest.split():
        key, equals, value = token.partition("=")
        if equals and re.fullmatch(r"[A-Za-z_]\w*", key):
            try:
                arguments[key] = _literal(value)
            except _Unreadable:
                return None
        elif token.startswith("<") and token.endswith(">") and positional not in arguments:
            arguments[positional] = Slot(token[1:-1])
        else:
            return None
    return arguments


def _pairs(text: str, pinned: list[tuple[Any, str]]) -> dict[str, Any] | None:
    """``offset=50, after_title="M"`` as arguments; None when they do not read one way."""
    pairs: dict[str, Any] = {}
    for match in _PAIR_PARTS.finditer(text):
        key, value = match["key"], match["value"]
        if key in pairs:
            return None
        if value.startswith("\x01"):
            pairs[key] = pinned[int(value.strip("\x01"))][0]
            continue
        try:
            pairs[key] = _literal(value)
        except _Unreadable:
            return None
    return pairs


# ---------------------------------------------------------------------------
# The structured continuation
# ---------------------------------------------------------------------------


def with_cursor(call: DoorCall, coverage: Any) -> DoorCall:
    """``call`` with the arguments that continue its reply, read from the reply's ``coverage``."""
    if call.cursor is not None or not isinstance(coverage, Mapping):
        return call
    cursor: dict[str, Any] = {}
    continue_with = coverage.get("continue_with")
    if isinstance(continue_with, Mapping) and continue_with:
        cursor = dict(continue_with)
    elif coverage.get("next_offset") is not None:
        cursor = {"offset": coverage["next_offset"]}
    elif coverage.get("next_cursor") is not None:
        cursor = {_CURSOR_ARGUMENT.get(call.command, "cursor"): coverage["next_cursor"]}
    return replace(call, cursor=cursor) if cursor else call


def _continued(call: DoorCall) -> str:
    """The command that continues ``call``: the call itself with its reply's cursor."""
    return shell_command(call.command, {**call.arguments, **(call.cursor or {})}, flags=call.flags)


def _spellings(value: Any) -> list[str]:
    """How the door may print ``value`` after ``key=``: longest first."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return []
    if isinstance(value, int):
        return [str(value)]
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    forms = {
        json.dumps(value),
        json.dumps(value, ensure_ascii=False),
        f'"{escaped}"',
        # The door printed titles raw until it escaped them.
        f'"{value}"',
    }
    if re.fullmatch(_PLAIN, value):
        forms.add(value)
    return sorted(forms, key=len, reverse=True)


def _pin(text: str, call: DoorCall | None, pinned: list[tuple[Any, str]]) -> str:
    """``text`` with each value of the structured cursor it prints replaced by a marker.

    The marker stands for the value itself, so however the door spelled it
    (escaped or not, a quote in the middle or not) the whole of it is one
    value, and what it holds is never parsed out of prose.
    """
    for key, value in (call.cursor or {}).items() if call is not None else ():
        if key not in _CONTINUATION_KEYS:
            continue
        for form in _spellings(value):
            needle = f"{key}={form}"
            found = re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", text)
            if found is not None:
                marker = f"{key}=\x01{len(pinned)}\x01"
                pinned.append((value, form))
                text = text[: found.start()] + marker + text[found.end() :]
                break
    return text


def _sources(text: str, call: DoorCall | None) -> str:
    """The exact forms a Sources line carries, as commands."""

    def integration(match: re.Match[str]) -> str:
        return f"beakr integrations call {match['tool']} --args {shlex.quote(match['arguments'])}"

    text = _INTEGRATION_CALL.sub(integration, text)
    text = re.sub(rf"\bread_file (?={_UUID})", "beakr documents read ", text)
    flag = "--full" if call is not None and call.command == "cat" else "--json"
    return _BRIEF_NOTE.sub(f"{flag} adds each document's excerpt, url and file id", text)


def _source_line(line: str, call: DoorCall | None) -> str:
    """A ``fetch:`` or ``read:`` line of a source entry, as the command that opens it."""
    fetch = _FETCH_LINE.match(line)
    if fetch is not None and not fetch["tool"].startswith("integrations"):
        pairs = re.split(r", (?=\w+=)", fetch["pairs"])
        options = " ".join(f"--arg {shlex.quote(pair)}" for pair in pairs)
        return f"{fetch['lead']}beakr integrations call {fetch['tool']} {options}"
    read = _READ_LINE.match(line)
    if read is not None:
        return f"{read['lead']}beakr documents read {read['id']}"
    if line.lstrip().startswith(("fetch: ", "read: ")):
        return _sources(line, call)
    return line


def shell_hint(text: str, call: DoorCall | None = None) -> str:
    """``text`` with every door call it names replaced by the command that makes it."""
    kept: list[str] = []
    pinned: list[tuple[Any, str]] = []
    cursor = dict(call.cursor) if call is not None and call.cursor else {}
    # The two characters that mark a kept command and a pinned value below: a
    # reply that carried one could name a marker that does not exist.
    text = text.replace("\x00", "").replace("\x01", "")

    def keep(command: str) -> str:
        # Out of reach of the later patterns, which would read a command's own
        # words (`--offset 50`, `cat`) as another call.
        kept.append(f"`{command}`")
        return f"\x00{len(kept) - 1}\x00"

    def braces(match: re.Match[str]) -> str:
        first = _brace_arguments(match["first"])
        if first is None:
            return match[0]
        if (
            call is not None
            and cursor
            and match["command"] == call.command
            and all(key in first and first[key] == value for key, value in cursor.items())
        ):
            # The door's own spelling of the continuation: the call `beakr kb`
            # made with the cursor, and the flags the door never saw.
            out = keep(_continued(call))
        else:
            out = keep(shell_command(match["command"], first))
        for more in _MORE.finditer(match["more"]):
            arguments = _brace_arguments(more["body"])
            if arguments is None:
                return match[0]
            out += f"{more['joiner']} {keep(shell_command(match['command'], arguments))}"
        return out

    def ticked(match: re.Match[str]) -> str:
        arguments = _ticked_arguments(match["command"], match["rest"])
        return match[0] if arguments is None else keep(shell_command(match["command"], arguments))

    def structured(match: re.Match[str]) -> bool:
        """Whether this continuation names the reply's own cursor, which is data."""
        command = match["command"] or (call.command if call else None)
        if not cursor or call is None or call.command != command:
            return False
        pairs = _pairs(match["pairs"], pinned)
        return pairs is not None and all(
            key in cursor and cursor[key] == value for key, value in pairs.items()
        )

    def continuation(match: re.Match[str]) -> str:
        pairs = _pairs(match["pairs"], pinned)
        command = match["command"] or (call.command if call else None)
        if pairs is None or command is None:
            return match[0]
        same = call is not None and call.command == command
        if call is not None and structured(match):
            # The reply says how to continue as data: the prose only placed it.
            again = _continued(call)
        else:
            base = dict(call.arguments) if call is not None and same else {}
            flags = call.flags if call is not None and same else ()
            again = shell_command(command, {**base, **pairs}, flags=flags)
        lead = match["lead"] or ""
        if match["command"]:
            lead = "run "
        elif lead.startswith("re-run"):
            lead = "re-run as "
        return lead + keep(again)

    def readable(text: str) -> bool:
        """Whether every cursor value the prose carries reads one way.

        Not when a cursor argument's quoted value is not one the pattern can
        read, or a quote is left over after a value read from prose: the door
        printed a title with its own quotes, the value ended somewhere else,
        and a command built from the rest (`after_page_id` without its
        `after_title`, half a title) would list another place. All the
        continuations of the text are then left as the door wrote them.
        """
        rest = _CONTINUATION.sub("", text)
        from_prose = any(
            '"' in match["pairs"] and not structured(match)
            for match in _CONTINUATION.finditer(text)
        )
        return not _UNREAD_PAIR.search(rest) and not (from_prose and '"' in rest)

    def proposal(match: re.Match[str]) -> str:
        target = call.proposal_id if call is not None and call.proposal_id else Slot("proposal_id")
        command = keep(f"beakr kb proposals {match['verb']} {shell_word(target)}")
        return ("run " if match["call"] else "") + command

    text = _pin(text, call, pinned)
    text = _TICKED_CALL.sub(ticked, text)
    text = _BRACE_CALL.sub(braces, text)
    if readable(text):
        text = _CONTINUATION.sub(continuation, text)
    elif call is not None and any(
        re.search(rf"(?<![\w-]){re.escape(key)}=", text) for key in cursor
    ):
        # The prose names the cursor in a way that cannot be read, and the reply
        # carries it as data: the prose stays, the command follows it.
        text = f"{text.rstrip()} Continue with {keep(_continued(call))}."
    text = _PROPOSAL_TOOL.sub(proposal, text)
    text = _WRITE_TOOL.sub(lambda m: keep(f"{GENERIC_WRITE} --action {m['action']}"), text)
    text = _KNOWLEDGE_TOOL.sub(lambda m: keep(shell_command(m["command"], {})), text)
    text = _BARE_COMMAND.sub(
        lambda m: keep(shell_command(m["command"], _BARE_ARGUMENTS.get(m["command"], {}))),
        text,
    )
    text = _DOCUMENTS.sub(lambda m: keep(_DOCUMENT_COMMANDS[m["verb"]]), text)
    text = _sources(text, call)
    text = re.sub(r"\bread_file\b", lambda m: keep(_DOCUMENT_COMMANDS["read"]), text)
    # A pinned value no command took stays as the door spelled it.
    text = re.sub(r"\x01(\d+)\x01", lambda m: pinned[int(m[1])][1], text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m[1])], text)


# ---------------------------------------------------------------------------
# A whole reply
# ---------------------------------------------------------------------------

#: Commands whose ``content`` is the organization's own text from end to end.
_BODY_COMMANDS = frozenset({"cat", "blame"})
#: How much of a project's files a search could reach: the door appends it to
#: ``sections`` and ``grep`` (the engine's ``kb_read_v2/corpus_coverage.py``).
#: Not the line ``rows`` prints about the same counts, which closes a table.
_CORPUS_NOTICE = re.compile(
    r"Knowledge was compiled from \d+ of \d+ file\(s\) in this project \(.*\)\. "
    r"This search covers what was compiled"
)


def shell_content(text: str, call: DoorCall, *, acknowledgement: bool = False) -> str:
    """A reply's ``content`` with the door's own notices translated, quoted text left alone.

    ``acknowledgement`` is a write's reply, which is the door's words throughout.
    """
    if call.command in _BODY_COMMANDS:
        return text
    lines = [_source_line(line, call) for line in text.split("\n")]
    if acknowledgement:
        return "\n".join(shell_hint(line, call) for line in lines)
    blank = [index for index, line in enumerate(lines) if not line.strip()]
    first_end = blank[0] if blank else len(lines)
    last_start = blank[-1] + 1 if blank else 0
    # The door's statement about the corpus closes a search after its footer,
    # so the paragraph before it is the door's closing notice too.
    while len(blank) > 1 and all(
        _CORPUS_NOTICE.match(line) for line in lines[last_start:] if line.strip()
    ):
        blank.pop()
        last_start = blank[-1] + 1
    return "\n".join(
        shell_hint(line, call) if index < first_end or index >= last_start else line
        for index, line in enumerate(lines)
    )


def shell_reply(tool: str, body: Mapping[str, Any], call: DoorCall) -> str:
    """A door reply as terminal text: :func:`beakr_cli.mcp_render.render`, with commands.

    ``tool`` is ``knowledge_base`` for a read and ``knowledge_base_write`` for a
    write, whose whole text is the door's acknowledgement.
    """
    from beakr_cli.mcp_render import render

    call = with_cursor(call, body.get("coverage"))
    shown = shell_content(
        str(body.get("content") or "").rstrip(),
        call,
        acknowledgement=tool == "knowledge_base_write",
    )
    hints = [shell_hint(str(hint), call) for hint in body.get("next") or [] if str(hint)]
    rendered = render(tool, {**body, "content": shown, "next": hints})
    if not shown or not rendered.startswith(shown):
        return rendered
    tail = [
        line
        if line.startswith(("Coverage: ", "next: "))
        else shell_hint(line, call)
        if line.startswith("Approval state")
        else _sources(line, call)
        for line in rendered[len(shown) :].split("\n")
    ]
    return shown + "\n".join(tail)
