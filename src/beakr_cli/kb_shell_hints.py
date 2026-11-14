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

What is translated is the door's guidance, and only where the reply says, by
its structure, that the text is guidance (review 2026-11-13: a listing with no
blank line had every line translated, so a page titled ``knowledge_base_write
action='delete_page'`` printed as a command the door never suggested):

- the reply's ``next`` steps (:func:`shell_hint`) and its ``coverage``, the
  structured continuation (:func:`with_cursor`, :func:`continuation`);
- a write's acknowledgement (:func:`shell_acknowledgement`), where the only
  command ever printed is ``beakr kb proposals show|accept|dismiss`` for the
  proposal that write staged;
- the lines that open a cited source, built from the reply's source records
  (:mod:`beakr_cli.kb_shell_sources`), never read out of a rendered line.

A read's ``content`` is the organization's text (titles, hits, excerpts, page
bodies) with the door's notices among it, and nothing in the text says which
line is which: it is printed as the door wrote it. A ``next`` step can itself
quote a title, so inside one a call is not read between double quotes, a write
action or a provider tool must be one the vendored tool surface names, and a
value holding a character a terminal hides prints no command at all.

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
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from beakr_cli.kb_shell_commands import (
    COMMANDS,
    DOOR_COMMANDS,
    GENERIC_WRITE,
    DoorCall,
    Slot,
    Unprintable,
    is_write_action,
    shell_command,
)
from beakr_cli.kb_shell_sources import (
    marked_sources,
    place_commands,
    source_lines,
    text_integration_calls,
    without_marks,
)

__all__ = [
    "DoorCall",
    "Slot",
    "continuation",
    "shell_acknowledgement",
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
_FILE_BY_ID = re.compile(rf"\bread_file (?P<id>{_UUID})\b")


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
        # `command` is the door's field for the command itself: arguments that
        # carry one name another call than the hint does.
        return None if "command" in parsed else parsed
    arguments: dict[str, Any] = {}
    for item in _split(body[1:-1]):
        match = _ITEM.fullmatch(item.strip())
        if match is None:
            return None
        name = match["name"]
        if name == "command":
            return None
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
        if equals and key == "command":
            return None
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


def continuation(call: DoorCall) -> str | None:
    """The command that reads on from ``call``'s reply, when its ``coverage`` says how.

    None when the reply carries no cursor, or one that cannot be printed.
    """
    if not call.cursor:
        return None
    try:
        return _continued(call)
    except Unprintable:
        return None


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


def _proposal_target(call: DoorCall | None) -> str:
    """The proposal a write staged, when its id is one: else the placeholder.

    The id is printed bare after the verb, so it must be an id and nothing an
    option parser or a shell could read another way.
    """
    staged = call.proposal_id if call is not None else None
    return staged if staged and re.fullmatch(_UUID, staged) else str(Slot("proposal_id"))


def shell_acknowledgement(text: str, call: DoorCall | None = None) -> str:
    """A write's reply with the proposal tools it names as the commands that run them.

    The reply also quotes the page it changed and what the write said, so the
    only command this prints is ``beakr kb proposals show|accept|dismiss`` for
    the proposal this write staged: whatever a quoted title holds, no other
    command can come of it.
    """
    target = _proposal_target(call)

    def proposal(match: re.Match[str]) -> str:
        command = f"`beakr kb proposals {match['verb']} {target}`"
        return ("run " if match["call"] else "") + command

    return _PROPOSAL_TOOL.sub(proposal, text)


def _quoted(text: str, position: int) -> bool:
    """Whether ``position`` is inside a double-quoted span of its line: a quoted title."""
    quoted = escaped = False
    for char in text[text.rfind("\n", 0, position) + 1 : position]:
        if char == '"' and not escaped:
            quoted = not quoted
        # A backslash escapes the next character only, and only inside quotes.
        escaped = quoted and char == "\\" and not escaped
    return quoted


def _guarded(translate: Callable[[re.Match[str]], str]) -> Callable[[re.Match[str]], str]:
    """``translate``, except where the door is quoting or a value cannot be printed."""

    def run(match: re.Match[str]) -> str:
        if _quoted(match.string, match.start()):
            return match[0]
        try:
            return translate(match)
        except Unprintable:
            return match[0]

    return run


def shell_hint(text: str, call: DoorCall | None = None) -> str:
    """A ``next`` step with every door call it names replaced by the command that makes it.

    For the door's guidance only (a reply's ``next``): never a listing, a page
    body or any text the organization wrote.
    """
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

    def continued(match: re.Match[str]) -> str:
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

    target = _proposal_target(call)

    def proposal(match: re.Match[str]) -> str:
        command = keep(f"beakr kb proposals {match['verb']} {target}")
        return ("run " if match["call"] else "") + command

    def write(match: re.Match[str]) -> str:
        # Only an action the write door has: a name from anywhere else is not one.
        if not is_write_action(match["action"]):
            return match[0]
        return keep(f"{GENERIC_WRITE} --action {match['action']}")

    text = _pin(text, call, pinned)
    text = _TICKED_CALL.sub(_guarded(ticked), text)
    text = _BRACE_CALL.sub(_guarded(braces), text)
    again = continuation(call) if call is not None else None
    if readable(text):
        text = _CONTINUATION.sub(_guarded(continued), text)
    elif again is not None and any(
        re.search(rf"(?<![\w-]){re.escape(key)}=", text) for key in cursor
    ):
        # The prose names the cursor in a way that cannot be read, and the reply
        # carries it as data: the prose stays, the command follows it.
        text = f"{text.rstrip()} Continue with {keep(again)}."
    text = _PROPOSAL_TOOL.sub(_guarded(proposal), text)
    text = _WRITE_TOOL.sub(_guarded(write), text)
    text = _KNOWLEDGE_TOOL.sub(_guarded(lambda m: keep(shell_command(m["command"], {}))), text)
    text = _BARE_COMMAND.sub(
        _guarded(
            lambda m: keep(shell_command(m["command"], _BARE_ARGUMENTS.get(m["command"], {})))
        ),
        text,
    )
    text = _DOCUMENTS.sub(_guarded(lambda m: keep(_DOCUMENT_COMMANDS[m["verb"]])), text)
    text = text_integration_calls(text, keep, skip=_quoted)
    text = _FILE_BY_ID.sub(_guarded(lambda m: keep(f"beakr documents read {m['id']}")), text)
    text = re.sub(r"\bread_file\b", _guarded(lambda m: keep(_DOCUMENT_COMMANDS["read"])), text)
    # A pinned value no command took stays as the door spelled it.
    text = re.sub(r"\x01(\d+)\x01", lambda m: pinned[int(m[1])][1], text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m[1])], text)


# ---------------------------------------------------------------------------
# A whole reply
# ---------------------------------------------------------------------------


def shell_content(
    text: str, call: DoorCall, *, acknowledgement: bool = False, sources: Any = None
) -> str:
    """A reply's ``content`` for a terminal.

    ``acknowledgement`` is a write's reply: :func:`shell_acknowledgement`. A
    read's content is the organization's text and is printed as the door wrote
    it, except a ``fetch:`` or ``read:`` line that one of the reply's
    ``sources`` records accounts for whole, which is replaced by the command
    built from that record.
    """
    if acknowledgement:
        return shell_acknowledgement(text, call)
    known = source_lines(sources)
    if not known:
        return text
    lines = []
    for line in text.split("\n"):
        body = line.lstrip()
        command = known.get(body)
        lines.append(line[: len(line) - len(body)] + command if command else line)
    return "\n".join(lines)


def _brief_note(call: DoorCall) -> str:
    """The line closing a brief Sources list, naming the flag that gives the whole one."""
    flag = "--full" if call.command == "cat" else "--json"
    return f"({flag} adds each document's excerpt, url and file id)"


def shell_reply(tool: str, body: Mapping[str, Any], call: DoorCall) -> str:
    """A door reply as terminal text: :func:`beakr_cli.mcp_render.render`, with commands.

    ``tool`` is ``knowledge_base`` for a read and ``knowledge_base_write`` for a
    write, whose content is the door's acknowledgement. The Sources list is
    rendered from records that carry a stand-in where a file id or a tool
    stands, and each is then replaced by the command built from the record.
    """
    from beakr_cli.mcp_render import _BRIEF_NOTE, render

    write = tool == "knowledge_base_write"
    # A stand-in holds a NUL: none anywhere in the reply, so none can be forged.
    body = without_marks(dict(body))
    call = with_cursor(call, body.get("coverage"))
    shown = shell_content(
        str(body.get("content") or "").rstrip(),
        call,
        acknowledgement=write,
        sources=body.get("sources"),
    )
    hints = [shell_hint(str(hint), call) for hint in body.get("next") or [] if str(hint)]
    again = continuation(call)
    if again is not None and not any(again in hint for hint in hints):
        # The reply says how to read on as data: the command is printed even
        # where no next step spells it.
        hints.append(f"Continue with `{again}`.")
    records, placed = marked_sources(body.get("sources"))
    rendered = render(tool, {**body, "content": shown, "next": hints, "sources": records})
    rendered = place_commands(rendered, placed)
    if not shown or not rendered.startswith(shown):
        return rendered
    tail = [
        _brief_note(call)
        if line == _BRIEF_NOTE
        else shell_acknowledgement(line, call)
        if write and line.startswith("Approval state")
        else line
        for line in rendered[len(shown) :].split("\n")
    ]
    return shown + "\n".join(tail)
