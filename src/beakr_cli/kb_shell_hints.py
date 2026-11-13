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
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping
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

__all__ = ["DoorCall", "Slot", "shell_command", "shell_content", "shell_hint", "shell_reply"]


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
_VALUE = r'"(?:[^"\\]|\\.)*"|<[^<>\n]*>|[^\s,;)"`]*[^\s,;)"`.]'
_PAIR = rf"(?<![\w-])(?:{_KEY})=(?:{_VALUE})"
_CONTINUATION = re.compile(
    rf"(?P<lead>(?:[Cc]all )?(?P<command>{_NAMES}) again with |re-run with |[Cc]ontinue with )?"
    r"(?:the same (?:filters|arguments) and )?"
    rf"(?P<pairs>{_PAIR}(?:,? (?:and )?{_PAIR})*)"
    r"(?: and the same filters)?"
)
_PAIR_PARTS = re.compile(rf'(?<![\w-])(?P<key>{_KEY})=(?P<value>"(?:[^"\\]|\\.)*"|\S+)')

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


def _literal(text: str) -> Any:
    text = text.strip()
    if text.startswith('"'):
        try:
            return json.loads(text)
        except ValueError:
            return text.strip('"')
    if text.startswith("<") and text.endswith(">"):
        return Slot(text[1:-1])
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return {"true": True, "false": False}.get(text, text)


def _split(inner: str) -> list[str]:
    """``inner`` at its commas, leaving the ones inside double quotes."""
    items, current, quoted = [], "", False
    for index, char in enumerate(inner):
        if char == '"' and (index == 0 or inner[index - 1] != "\\"):
            quoted = not quoted
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
        arguments[name] = (
            Slot(name, optional=bool(match["optional"]))
            if match["value"] is None
            else _literal(match["value"])
        )
    return arguments


def _ticked_arguments(command: str, rest: str) -> dict[str, Any] | None:
    """``<page> section_id=<id>`` as arguments: a bare ``<name>`` is the positional."""
    spec = COMMANDS.get(command)
    positional = spec.positional if spec is not None and spec.positional else "page"
    arguments: dict[str, Any] = {}
    for token in rest.split():
        key, equals, value = token.partition("=")
        if equals and re.fullmatch(r"[A-Za-z_]\w*", key):
            arguments[key] = _literal(value)
        elif token.startswith("<") and token.endswith(">") and positional not in arguments:
            arguments[positional] = Slot(token[1:-1])
        else:
            return None
    return arguments


def _pairs(text: str) -> dict[str, Any]:
    return {
        match["key"]: _literal(
            match["value"].rstrip(".,;)") if not match["value"].startswith('"') else match["value"]
        )
        for match in _PAIR_PARTS.finditer(text)
    }


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

    def keep(command: str) -> str:
        # Out of reach of the later patterns, which would read a command's own
        # words (`--offset 50`, `cat`) as another call.
        kept.append(f"`{command}`")
        return f"\x00{len(kept) - 1}\x00"

    def braces(match: re.Match[str]) -> str:
        first = _brace_arguments(match["first"])
        if first is None:
            return match[0]
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

    def continuation(match: re.Match[str]) -> str:
        pairs = _pairs(match["pairs"])
        command = match["command"] or (call.command if call else None)
        if command is None:
            return match[0]
        same = call is not None and call.command == command
        base = dict(call.arguments) if same else {}
        again = shell_command(command, {**base, **pairs})
        if same and again.startswith("beakr kb "):
            again = " ".join([again, *call.flags])
        lead = match["lead"] or ""
        if match["command"]:
            lead = "run "
        elif lead.startswith("re-run"):
            lead = "re-run as "
        return lead + keep(again)

    def proposal(match: re.Match[str]) -> str:
        target = call.proposal_id if call is not None and call.proposal_id else Slot("proposal_id")
        command = keep(f"beakr kb proposals {match['verb']} {shell_word(target)}")
        return ("run " if match["call"] else "") + command

    text = _TICKED_CALL.sub(ticked, text)
    text = _BRACE_CALL.sub(braces, text)
    text = _CONTINUATION.sub(continuation, text)
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
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m[1])], text)


# ---------------------------------------------------------------------------
# A whole reply
# ---------------------------------------------------------------------------

#: Commands whose ``content`` is the organization's own text from end to end.
_BODY_COMMANDS = frozenset({"cat", "blame"})


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
