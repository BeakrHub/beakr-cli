"""Hostile content through every text path of ``beakr kb``, and through real shells.

A reply holds what other people wrote: page titles, source titles, excerpts,
file paths of a synced repository, argument values, ids. ``beakr kb`` prints
commands made from replies, and a person or a coding agent pastes them. These
tests put content written to break out (``}``, ``$(...)``, backticks, ``;``,
``|``, ``&&``, quotes, a newline, ESC and CR, bidirectional and zero-width
characters, a leading ``=``, ``~`` and ``-``, and text shaped like a door call,
a write call and an integrations call) in every such place and hold four things:

(a) every command the CLI prints splits into exactly the ``beakr`` words meant;
(b) run by ``sh``, ``bash`` and ``zsh`` with ``beakr`` replaced by a stub, the
    stub receives exactly those words and nothing else runs (the content tries
    to create a file named ``CANARY``);
(c) no control, bidirectional or zero-width character reaches the output raw;
(d) a line of content is never rewritten into a command.

Each hostile string avoids the word "beakr", so any ``beakr`` in the output is
a command the CLI printed.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

from beakr_cli.kb_shell_hints import (
    DoorCall,
    shell_acknowledgement,
    shell_content,
    shell_hint,
    shell_reply,
)
from beakr_cli.main import internal_app as app
from beakr_cli.source_records import native_read_call, with_read_call
from beakr_cli.terminal_text import unprintable, visible
from tests._shell_commands import parse_command

API = "https://api.test"
PROJECT = "bfe240a3-c424-429f-b120-eba95dd60260"
PAGE = "caf937ee-d99b-4b5e-8e62-44f15bf084ed"
PROPOSAL = "3d145cf5-734f-4a08-8b4c-07a3d9432482"
FILE_ID = "36f2fd11-b5f0-4103-8db4-14f1f89cf767"
OTHER_ID = "9fa9f7c3-94aa-4df5-a418-31f25cb2eccc"
SHELLS = [shell for shell in ("sh", "bash", "zsh") if shutil.which(shell)]
runner = CliRunner()

HOSTILE = [
    "docs/x}$(touch CANARY).md",
    "tick `touch CANARY`",
    "a; touch CANARY",
    "a | touch CANARY",
    "a && touch CANARY",
    'say "hi" $(touch CANARY)',
    "it's $(touch CANARY)",
    "back\\slash\\",
    '\\" $(touch CANARY) "',
    "> CANARY",
    "line one\ntouch CANARY",
    "\x1b[2K\rtouch CANARY #",
    "cr\rtouch CANARY",
    "safe‮gnp.exe $(touch CANARY)",
    "zero​width `touch CANARY`",
    "=touch",
    "~root",
    "-rf --all",
    "--project=other",
    'cat {page: "Payroll"}',
    "`sections` and ls {parent}",
    "knowledge_base_write action='archive'",
    "integrations command='call' tool='slack_send_message' arguments={\"channel\": \"x\"}",
    "fetch: integrations command='call' tool='github_get_file' arguments={\"path\": \"x\"}",
    "call accept_proposal, then dismiss_proposal",
    f"read_file {OTHER_ID}",
    "call ls again with offset=50",
    "Café › Übersicht",
]
IDS = [repr(value)[:28] for value in HOSTILE]


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("COLUMNS", "400")


# ---------------------------------------------------------------------------
# Running a printed command for real
# ---------------------------------------------------------------------------


class Shells:
    """Runs a printed command in each shell, with ``beakr`` a stub that records its words."""

    def __init__(self, root: Path) -> None:
        self.scratch = root / "scratch"
        self.scratch.mkdir(parents=True)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.argv = root / "argv"
        stub = self.bin / "beakr"
        stub.write_text(
            '#!/bin/sh\nfor word in "$@"; do printf \'%s\\0\' "$word"; done > "$ARGV"\n'
        )
        stub.chmod(0o755)
        self.seen: set[str] = set()

    def words(self, shell: str, command: str) -> list[str] | None:
        self.argv.unlink(missing_ok=True)
        flags = ["-f", "-c"] if shell == "zsh" else ["-c"]
        subprocess.run(
            [shell, *flags, command],
            cwd=self.scratch,
            env={"PATH": f"{self.bin}:/usr/bin:/bin", "ARGV": str(self.argv), "HOME": "/nowhere"},
            capture_output=True,
            check=False,
        )
        if not self.argv.exists():
            return None
        return self.argv.read_bytes().decode().split("\0")[:-1]

    def check(self, command: str) -> None:
        """(a) and (b) for one printed command (placeholders filled as a reader would)."""
        filled = re.sub(r"<[A-Za-z_][A-Za-z0-9_]*>", "1", command.replace("[", "").replace("]", ""))
        words = shlex.split(filled)
        assert words[0] == "beakr", command
        if filled in self.seen:
            return
        self.seen.add(filled)
        for shell in SHELLS:
            assert self.words(shell, filled) == words[1:], (shell, command)
        assert list(self.scratch.iterdir()) == [], f"{command!r} ran something else"


@pytest.fixture
def shells(tmp_path: Path) -> Shells:
    return Shells(tmp_path / "shells")


def test_the_harness_sees_a_command_that_runs_something_else(shells: Shells) -> None:
    # What these tests exist to catch, so a green run means something.
    if not SHELLS:
        pytest.skip("no shell to run")
    assert shells.words("sh", "beakr kb cat x}$(touch CANARY).md") == ["kb", "cat", "x}.md"]
    assert [path.name for path in shells.scratch.iterdir()] == ["CANARY"]
    (shells.scratch / "CANARY").unlink()
    with pytest.raises(AssertionError):
        shells.check("beakr kb cat 'x'; touch CANARY")


# ---------------------------------------------------------------------------
# A reply with the hostile string in every place a reply has
# ---------------------------------------------------------------------------

_GRANOLA = {"note_id": "not_LiveRf00000001"}


def _github(hostile: str) -> dict[str, Any]:
    return {"owner": "o", "repo": "r", "path": hostile}


def _content(hostile: str) -> str:
    """A listing with the string as a title, a hit's text and a line of its own."""
    return (
        f"3 matching page(s):\n  - [topic] {hostile}  [Helios Team]\n"
        f"      {hostile}\n{hostile}\n"
        f"     fetch: slack_send_message with channel={hostile}\n"
        f"Read a hit with cat {{page, section_id}} or call accept_proposal: {hostile}"
    )


def _sources(hostile: str) -> list[dict[str, Any]]:
    return [
        {
            "name": f"{hostile}.md",
            "filename": f"{hostile}.md",
            "location": {
                "display_path": f"docs/{hostile}.md",
                "file_id": FILE_ID,
                "url": f"https://files.test/{hostile}",
                "unit_locator": hostile,
            },
            "source_ref": "source-ref-1",
            "evidence": {"excerpt": hostile},
        },
        {
            "name": hostile,
            "provider": "github",
            "location": {"display_path": hostile},
            "evidence": {"excerpt": f"{hostile} as agreed"},
            "retrieval": {"tool": "github_get_file", "arguments": _github(hostile)},
        },
        {
            "name": f"Granola: {hostile}",
            "provider": "granola",
            "retrieval": {"tool": "granola_get_note", "arguments": _GRANOLA},
        },
    ]


def _next(hostile: str) -> list[str]:
    return [
        "Not everything is shown: continue with the same filters and "
        f"after_title={json.dumps(hostile, ensure_ascii=False)} to read the rest.",
        f"No page is titled {json.dumps(hostile)}; open one with cat {{page}}.",
        "Review one with show_proposal, then accept_proposal.",
    ]


def _reply(hostile: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": _content(hostile)}],
        "outcome": {"status": "success"},
        "coverage": {"complete": False, "continue_with": {"after_title": hostile}},
        "next": _next(hostile),
        "sources": _sources(hostile),
    }


def _integration(tool: str, arguments: dict[str, Any]) -> list[str]:
    return [
        "beakr",
        "integrations",
        "call",
        tool,
        "--args",
        json.dumps(arguments, ensure_ascii=False),
    ]


def _take(output: str, pattern: str) -> tuple[str, str]:
    """The one command ``pattern`` frames in ``output``, and the output without it."""
    found = re.findall(pattern, output, flags=re.MULTILINE)
    assert len(found) == 1, (pattern, output)
    return found[0], output.replace(found[0], "", 1)


def _no_hidden_characters(output: str) -> None:
    """(c): what a terminal would hide or obey is shown as an escape, never written."""
    assert not unprintable(output.replace("\n", "")), repr(output)


def _check_door_reply(
    output: str, hostile: str, shells: Shells, *, continues: str = "kb ls"
) -> None:
    """(a) to (d) for a door reply printed from :func:`_reply`."""
    _no_hidden_characters(output)
    # (d) The listing is printed as the door wrote it, line for line.
    assert output.startswith(visible(_content(hostile)) + "\n"), output
    rest = output[len(visible(_content(hostile))) :]
    printable = not unprintable(hostile)

    # The commands the reply's own structure gives, and no other.
    commands: list[tuple[str, list[str] | None]] = []
    granola, rest = _take(rest, r" · read with (beakr integrations call granola_get_note .*)$")
    commands.append((granola, _integration("granola_get_note", _GRANOLA)))
    read, rest = _take(rest, rf" · (beakr documents read {FILE_ID}) · ")
    commands.append((read, ["beakr", "documents", "read", FILE_ID]))
    if printable:
        github, rest = _take(rest, r" · read with (beakr integrations call github_get_file .*)$")
        commands.append((github, _integration("github_get_file", _github(hostile))))
        again, rest = _take(
            rest, r"^next: Not everything is shown: continue with `(.*)` to read the rest\.$"
        )
        # The command that was run, with the reply's cursor; where that command
        # has no flag for the cursor, the door's own command with the same call.
        path, given = parse_command(again)
        assert path in (continues, "api knowledge run-wiki-command"), again
        if path == continues:
            assert given["after_title"] == hostile, again
        else:
            sent = json.loads(given["data"]) if "data" in given else given
            assert hostile in (sent.get("after_title"), sent.get("b__after_title")), again
        commands.append((again, None))
    else:
        # No command is printed for a value a terminal would not show as it is.
        assert "integrations command='call' tool='github_get_file'" in rest
        assert "continue with the same filters and after_title=" in rest
    for command in (
        "beakr kb cat <page>",
        "beakr kb proposals show <proposal_id>",
        "beakr kb proposals accept <proposal_id>",
    ):
        assert rest.count(f"`{command}`") == 1, (command, output)
        rest = rest.replace(f"`{command}`", "", 1)
        commands.append((command, None))
    assert "beakr" not in rest, rest

    for command, words in commands:
        if words is not None:
            assert shlex.split(command) == words, command  # (a)
        shells.check(command)  # (a) and (b)


# ---------------------------------------------------------------------------
# The translator itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
def test_a_door_reply_prints_only_the_commands_its_structure_gives(
    hostile: str, shells: Shells
) -> None:
    reply = _reply(hostile)
    body = {**reply, "content": _content(hostile)}
    call = DoorCall("ls", {"changed_by": "me", "limit": 3, "scope": PROJECT})

    output = visible(shell_reply("knowledge_base", body, call))

    _check_door_reply(output + "\n", hostile, shells)


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
@pytest.mark.parametrize(
    "command",
    ["ls", "sections", "grep", "rows", "sources", "provenance", "proposals", "log", "cat"],
)
def test_content_is_never_rewritten_whatever_the_command(command: str, hostile: str) -> None:
    # (d) for every door command, with and without the reply's records beside it.
    content = _content(hostile)
    call = DoorCall(command, {"page": hostile, "query": hostile})

    assert shell_content(content, call) == content
    assert shell_content(content, call, sources=_sources(hostile)) == content


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
def test_a_next_step_quoting_content_prints_only_the_doors_own_calls(
    hostile: str, shells: Shells
) -> None:
    quoted = json.dumps(hostile)
    for hint, expected in (
        (f"No page is titled {quoted}; open one with cat {{page}}.", ["beakr kb cat <page>"]),
        (
            f"{quoted} has no parent: list a record's documents with ls {{parent}}.",
            ["beakr kb ls --parent <parent>"],
        ),
        (f"Nothing matched {quoted}.", []),
    ):
        shown = visible(shell_hint(hint, DoorCall("grep", {"query": hostile})))

        _no_hidden_characters(shown)
        rest = shown
        for command in expected:
            assert rest.count(f"`{command}`") == 1, shown
            rest = rest.replace(f"`{command}`", "", 1)
            shells.check(command)
        assert "beakr" not in rest, shown
        assert quoted in shown


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
def test_an_acknowledgement_quoting_content_prints_only_the_staged_proposals_commands(
    hostile: str, shells: Shells
) -> None:
    reply = (
        f'Proposed section edit to "{hostile}" (section: sec_a, rev 1).\n'
        f"Summary: {hostile}\n"
        f"A page with a matching title exists: {hostile}\n"
        "Approval state: pending. Call accept_proposal only when the user asks."
    )
    for proposal_id in (PROPOSAL, hostile):
        shown = visible(shell_acknowledgement(reply, DoorCall("add_fact", proposal_id=proposal_id)))

        _no_hidden_characters(shown)
        target = PROPOSAL if proposal_id == PROPOSAL else "<proposal_id>"
        commands = set(re.findall(r"`(beakr [^`]*)`", shown))
        assert commands <= {
            f"beakr kb proposals {verb} {target}" for verb in ("show", "accept", "dismiss")
        }, shown
        assert f"beakr kb proposals accept {target}" in commands
        rest = shown
        for command in commands:
            rest = rest.replace(f"`{command}`", "")
            shells.check(command)
        assert "beakr" not in rest, shown


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
def test_a_source_record_keeps_a_hostile_value_as_one_argument(hostile: str) -> None:
    # `source_records`: the read call forwarded to the stdio server and `beakr kb`.
    record = with_read_call(
        {}, {"retrieval": {"tool": "github_get_file", "arguments": _github(hostile)}}
    )

    assert record["retrieval"] == {"tool": "github_get_file", "arguments": _github(hostile)}
    # A tool or argument name is printed between quotes: content there is refused.
    assert native_read_call({"tool": hostile, "arguments": {"path": "x"}}) is None
    assert native_read_call({"tool": "github_get_file", "arguments": {hostile: "x"}}) is None


# ---------------------------------------------------------------------------
# Every `beakr kb` command that prints a door reply
# ---------------------------------------------------------------------------

DOOR_READS = [
    (["sections", "rules"], "kb sections"),
    (["grep", "rules"], "kb grep"),
    (["ls", "--changed-by", "me"], "kb ls"),
    (["log", "--by", "me"], "kb log"),
    (["history", "--by", "me"], "kb log"),
    (["hover", "Agent rules"], "kb hover"),
    (["provenance", "Agent rules"], "kb provenance"),
    (["links", "Agent rules"], "kb links"),
    (["timeline"], "kb timeline"),
    (["proposals", "ls", "--by", "me"], "kb proposals ls"),
]


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
@respx.mock
def test_every_door_read_prints_hostile_content_safely(hostile: str, shells: Shells) -> None:
    reply = _reply(hostile)
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=Response(200, json=reply))

    for arguments, path in DOOR_READS:
        result = runner.invoke(app, ["kb", *arguments, "--project", PROJECT])

        assert result.exit_code == 0, result.output
        _check_door_reply(result.stdout, hostile, shells, continues=path)
    # --json is the door's reply, byte for byte what it sent.
    as_json = runner.invoke(app, ["kb", "ls", "--changed-by", "me", "--project", PROJECT, "--json"])
    assert json.loads(as_json.stdout) == reply


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
@respx.mock
def test_kb_cat_prints_a_hostile_page_safely(hostile: str, shells: Shells) -> None:
    page = {"id": PAGE, "title": hostile, "project_id": PROJECT}
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE}").mock(return_value=Response(200, json=page))
    reply = {**_reply(hostile), "coverage": {"complete": True}}
    reply["next"] = _next(hostile)[1:]
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=Response(200, json=reply))

    result = runner.invoke(app, ["kb", "cat", PAGE])

    assert result.exit_code == 0, result.output
    output = result.stdout
    _no_hidden_characters(output)
    assert output.startswith(visible(_content(hostile)) + "\n")
    rest = output[len(visible(_content(hostile))) :]
    for command in re.findall(r"read with (beakr integrations call .*)$", rest, flags=re.MULTILINE):
        shells.check(command)
        rest = rest.replace(command, "", 1)
    for command in (
        f"beakr documents read {FILE_ID}",
        "`beakr kb cat <page>`",
        "`beakr kb proposals show <proposal_id>`",
        "`beakr kb proposals accept <proposal_id>`",
    ):
        assert rest.count(command) == 1, output
        rest = rest.replace(command, "", 1)
    assert "beakr" not in rest, rest


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
@respx.mock
def test_kb_rows_prints_hostile_rows_safely(hostile: str, shells: Shells) -> None:
    reply = _reply(hostile)
    reply["coverage"] = {"complete": False, "next_cursor": hostile}
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=Response(200, json=reply))

    result = runner.invoke(app, ["kb", "rows", "--entity", "x", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    output = result.stdout
    _no_hidden_characters(output)
    assert output.startswith(visible(_content(hostile)) + "\n")
    rest = output[len(visible(_content(hostile))) :]
    if unprintable(hostile):
        assert "beakr" not in rest, rest
        return
    again, rest = _take(rest, r"^next: Continue with `(.*)`\.$")
    assert "beakr" not in rest, rest
    path, given = parse_command(again)
    assert path == "kb rows" and given["after_card"] == hostile and given["entity"] == "x"
    shells.check(again)


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
@respx.mock
def test_a_kb_write_prints_a_hostile_acknowledgement_safely(hostile: str, shells: Shells) -> None:
    text = (
        f'Proposed section edit to "{hostile}" (section: sec_findings, rev 1).\n'
        f"Proposal id: {PROPOSAL}\nSummary: {hostile}\n"
        "Approval state: pending. It waits for someone to accept it in Beakr; call "
        "accept_proposal only when the user asks you to accept this proposal."
    )
    reply = {
        "content": [{"type": "text", "text": text}],
        "outcome": {"status": "success"},
        "proposal": {"id": PROPOSAL, "content_hash": "15e3"},
    }
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=Response(200, json=reply))
    arguments = ["kb", "add-finding", "Compound NW-250", "--date", "2026-11-13", "--kind", "lesson"]
    arguments += ["--text", "Kept", "--rationale", "Check.", "--project", PROJECT]
    arguments += ["--evidence", "(stability-protocol.md › block 5)"]

    result = runner.invoke(app, arguments)

    assert result.exit_code == 0, result.output
    output = result.stdout
    _no_hidden_characters(output)
    accept = f"beakr kb proposals accept {PROPOSAL}"
    assert f"run `{accept}` only when the user asks" in output
    rest = output
    for verb in ("show", "accept", "dismiss"):
        rest = rest.replace(f"`beakr kb proposals {verb} {PROPOSAL}`", "")
    assert "beakr" not in rest, output
    shells.check(accept)
    assert json.loads(runner.invoke(app, [*arguments, "--json"]).stdout) == reply


# ---------------------------------------------------------------------------
# The `beakr kb` commands that print API fields, not a door reply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("piped", [True, False], ids=["piped", "terminal"])
@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
@respx.mock
def test_kb_commands_that_print_api_fields_write_no_hidden_character(
    hostile: str, piped: bool, monkeypatch
) -> None:
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: piped)
    pages = f"{API}/v1/knowledge/wiki/pages"
    hit = {
        "id": PAGE,
        "title": hostile,
        "breadcrumb": hostile,
        "summary": hostile,
        "content_snippet": hostile,
        "page_type": "topic",
        "score": 0.9,
    }
    listing = {"data": [hit], "next_cursor": None, "has_more": False}
    respx.get(url__regex=rf"{API}/v1/knowledge/wiki/search.*").respond(json=listing)
    respx.get(url__regex=rf"{pages}(\?.*)?$").respond(json=listing)
    respx.get(f"{pages}/{PAGE}").respond(json={**hit, "project_id": PROJECT})
    source = {"source_title": hostile, "filename": hostile, "key": hostile, "stance": "support"}
    respx.get(f"{pages}/{PAGE}/sources").respond(
        json={"data": [source], "next_cursor": None, "has_more": False}
    )
    proposal = {
        "id": PROPOSAL,
        "proposal_type": "edit",
        "status": "pending",
        "page_title": hostile,
        "summary": hostile,
        "payload": {"content": hostile},
    }
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").respond(json=proposal)

    for arguments in (
        ["search", "roadmap"],
        ["ls", "--project", PROJECT],
        ["sources", PAGE],
        ["proposals", "show", PROPOSAL],
    ):
        result = runner.invoke(app, ["kb", *arguments])

        assert result.exit_code == 0, (arguments, result.output)
        _no_hidden_characters(result.output.replace("\t", ""))
