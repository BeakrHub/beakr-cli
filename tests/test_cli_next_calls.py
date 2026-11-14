"""A reply's structured next steps (``next_calls``) print as runnable ``beakr`` commands.

Root cause (sandbox run, 2026-11-14): about thirty hint lines reached the
terminal in MCP wording ("Read a hit with cat {page, section_id}", "call rows
again with offset=3", "pass status=accepted", ``knowledge_base_write
action='add_finding'``, "continue with start_line=21"): prose does not say
which tool, arguments or values a step means, so the translator could not
turn them into commands. The engine now sends the same steps as data,
``next_calls``; these tests feed recorded sandbox replies with that field
added (``tests/fixtures/sandbox_next_calls.json``) through the real commands,
parse every printed command against the real Click tree, and run the
hostile-content checks over the new path. A reply without ``next_calls`` is
rendered from its prose exactly as before.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

from beakr_cli.kb_shell_hints import DoorCall, next_lines, shell_reply, with_cursor
from beakr_cli.kb_shell_next import next_block, next_steps
from beakr_cli.main import internal_app as app
from beakr_cli.terminal_text import unprintable, visible
from tests._shell_commands import parse_command
from tests.test_kb_hostile_content import HOSTILE, IDS, Shells

API = "https://api.test"
PROJECT = "9135d3be-d08e-4fa4-af6c-de78d128521b"
PAGE = "85e52f3b-4255-4471-848f-1b9eb0832196"
PROPOSAL = "e5632ad8-9658-406f-b12c-e204da797e0b"
REPLIES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "sandbox_next_calls.json").read_text()
)
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("COLUMNS", "400")


@pytest.fixture
def shells(tmp_path: Path) -> Shells:
    return Shells(tmp_path / "shells")


def _door_reply(name: str, *, structured: bool = True) -> dict[str, Any]:
    recorded = REPLIES[name]
    reply: dict[str, Any] = {
        "content": [{"type": "text", "text": recorded["content"]}],
        "outcome": {"status": "success"},
        "coverage": recorded.get("coverage") or {"complete": True},
        "next": recorded["next"],
        "sources": recorded.get("sources") or [],
    }
    if structured:
        reply["next_calls"] = recorded["next_calls"]
    return reply


def _block(output: str) -> list[str]:
    """The lines of the ``Next:`` block (empty when there is none)."""
    lines = output.split("\n")
    if "Next:" not in lines:
        return []
    return lines[lines.index("Next:") + 1 :]


def _commands(block: list[str]) -> list[str]:
    """Each command line of a ``Next:`` block, without its template note."""
    out = []
    for line in block:
        if line.startswith("  beakr "):
            out.append(
                re.sub(r"    \((?:template: replace |prints JSON)[^)]*\)$", "", line.strip())
            )
    return out


def _check_all_parse(commands: list[str], shells: Shells) -> None:
    assert commands
    for command in commands:
        parse_command(command)
        shells.check(command)


# ---------------------------------------------------------------------------
# Recorded replies through the real commands
# ---------------------------------------------------------------------------

#: (fixture, the `beakr` arguments that read it, commands the block must print)
READS = [
    (
        "sections",
        ["kb", "sections", "how does Beakr handle customer onboarding", "-P", PROJECT],
        [
            f"beakr kb cat <page> --section-id <section_id> --project {PROJECT}",
            f"beakr kb hover <page> --project {PROJECT}",
        ],
    ),
    (
        "grep_empty",
        ["kb", "grep", "zzqxnonexistentterm9917", "-P", PROJECT],
        [
            f"beakr kb cat <page> --section-id <section_id> --project {PROJECT}",
            # In the project the read ran in.
            f"beakr documents grep zzqxnonexistentterm9917 --project {PROJECT}",
        ],
    ),
    (
        "proposals_none",
        ["kb", "proposals", "ls", "--status", "accepted", "--by", "Zebulon Nonexistent"],
        ["beakr kb proposals ls --status accepted --by 'Zebulon Nonexistent'"],
    ),
    (
        "proposals_listing",
        ["kb", "proposals", "ls", "--by", "me"],
        [
            "beakr kb proposals show <proposal_id>",
            "beakr kb proposals accept <proposal_id> --reviewed-hash <reviewed_hash>",
            "beakr kb proposals dismiss <proposal_id>",
        ],
    ),
    (
        "log_cut",
        ["kb", "log", "--by", "me", "-P", PROJECT],
        [
            # The command that was run, with the reply's cursor.
            f"beakr kb log --by me --project {PROJECT} --offset 20",
            "beakr api knowledge run-wiki-command --command diff --page <page> --rev <rev> "
            f"--project {PROJECT}",
            "beakr api knowledge run-wiki-command --command show --page <page> --rev <rev> "
            f"--project {PROJECT}",
        ],
    ),
    (
        "sources",
        ["kb", "sources", PAGE, "-P", PROJECT],
        [
            'beakr integrations call slack_list_messages --args \'{"channel_id": "C0AMUSP0YMB"}\'',
            f"beakr documents read <file_id> --project {PROJECT}",
        ],
    ),
]


@pytest.mark.parametrize(("name", "arguments", "expected"), READS, ids=[r[0] for r in READS])
@respx.mock
def test_a_recorded_reply_with_next_calls_prints_runnable_commands(
    name: str, arguments: list[str], expected: list[str], shells: Shells
) -> None:
    page = {"id": PAGE, "title": "Beakr's Lightfield Integration", "project_id": PROJECT}
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE}").mock(return_value=Response(200, json=page))
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=Response(200, json=_door_reply(name))
    )

    result = runner.invoke(app, arguments)

    assert result.exit_code == 0, result.output
    out = result.stdout
    # The reply's text is printed as the door wrote it.
    assert out.startswith(REPLIES[name]["content"].split("\n")[0])
    block = _block(out)
    commands = _commands(block)
    assert commands[: len(expected)] == expected, block
    _check_all_parse(commands, shells)
    # Each command's why follows it.
    for entry in REPLIES[name]["next_calls"]:
        assert f"      {entry['why']}" in block, block
    # The prose steps that name calls are not printed beside the block.
    assert "next:" not in out.split("Next:")[0].split(REPLIES[name]["content"])[-1]
    # A placeholder marks its line as a template.
    for line in block:
        if "<" in line and line.startswith("  beakr "):
            assert "(template: replace <" in line, line


@pytest.mark.parametrize("name", [r[0] for r in READS])
@respx.mock
def test_a_reply_without_next_calls_is_rendered_from_its_prose_as_before(name: str) -> None:
    page = {"id": PAGE, "title": "Beakr's Lightfield Integration", "project_id": PROJECT}
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE}").mock(return_value=Response(200, json=page))
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=Response(200, json=_door_reply(name, structured=False))
    )
    arguments = next(r[1] for r in READS if r[0] == name)

    out = runner.invoke(app, arguments).stdout

    assert "Next:" not in out.split("\n")
    assert out.count("next: ") >= len(REPLIES[name]["next"])


def test_a_prose_step_that_names_no_call_is_kept_beside_the_block() -> None:
    reply = {
        "content": "3 hits",
        "next": [
            "Cite what you use with its readable reference (memo.pdf › p. 7).",
            "Open a hit with cat {page, section_id}.",
        ],
        "next_calls": [
            {
                "tool": "knowledge_base",
                "command": "cat",
                "arguments": {"page": "<page>", "section_id": "<section_id>"},
                "why": "Open a hit.",
            }
        ],
    }
    prose, block = next_lines(reply, DoorCall("sections", {"query": "x"}))

    assert prose == ["Cite what you use with its readable reference (memo.pdf › p. 7)."]
    assert block[1].startswith("  beakr kb cat <page> --section-id <section_id>")


def test_the_replys_own_continuation_runs_the_command_that_was_made() -> None:
    # `kb ls` filtered by author: the continuation repeats the filters and --all.
    reply = {
        **{k: v for k, v in REPLIES["ls_cut"].items() if k != "request"},
        "next_calls": [
            {
                "tool": "knowledge_base",
                "command": "ls",
                # The whole call, as the engine sends it.
                "arguments": {"changed_by": "me", "limit": 30, "after_title": "Beakr"},
                "why": "Read the rest.",
            },
            {
                "tool": "knowledge_base",
                "command": "ls",
                "arguments": {"parent": "<parent>"},
                "why": "List a record's documents.",
            },
        ],
    }
    call = DoorCall("ls", {"changed_by": "me", "limit": 30}, flags=("--all",))

    block = next_block(next_steps(reply["next_calls"], with_cursor(call, reply["coverage"])) or [])
    commands = _commands(block)

    assert commands[0] == "beakr kb ls --changed-by me --limit 30 --after-title Beakr --all"
    # The pasted step reads the same projects: not "No project is selected".
    assert commands[1] == "beakr kb ls --parent <parent> --all"
    for command in commands:
        parse_command(command)


@respx.mock
def test_research_prints_the_save_and_deep_steps_as_commands(shells: Shells) -> None:
    recorded = REPLIES["research"]
    respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=Response(
            200,
            json={
                "text": recorded["text"],
                "citations": [],
                "next": recorded["next"],
                "next_calls": recorded["next_calls"],
            },
        )
    )

    result = runner.invoke(app, ["research", recorded["request"]["query"], "-P", PROJECT])

    assert result.exit_code == 0, result.output
    commands = _commands(_block(result.stdout))
    query = shlex.quote(recorded["request"]["query"])
    assert commands == [
        f"beakr research {query} --deep --project {PROJECT}",
        "beakr kb add-finding <page> --date <date> --kind result --text <text> "
        f"--evidence <evidence> --rationale <rationale> --project {PROJECT}",
        "beakr projects ls",
    ]
    assert "knowledge_base_write action='add_finding'" not in result.stdout
    _check_all_parse(commands, shells)


@respx.mock
def test_documents_read_prints_the_next_window_as_a_command(shells: Shells) -> None:
    recorded = REPLIES["documents_read"]
    respx.post(f"{API}/v1/knowledge/documents").mock(
        return_value=Response(
            200,
            json={
                "content": recorded["content"],
                "next": recorded["next"],
                "next_calls": recorded["next_calls"],
            },
        )
    )

    result = runner.invoke(
        app, ["documents", "read", recorded["request"]["arguments"]["document"], "-P", PROJECT]
    )

    assert result.exit_code == 0, result.output
    commands = _commands(_block(result.stdout))
    assert commands == [
        f"beakr documents read 2e0f2ad2-dd4f-41ae-ad2f-a890db6c8934 --start-line 21 "
        f"--project {PROJECT}"
    ]
    assert "start_line=21" not in result.stdout
    _check_all_parse(commands, shells)


# ---------------------------------------------------------------------------
# Every tool value
# ---------------------------------------------------------------------------


def _one(entry: dict[str, Any], call: DoorCall | None = None, tool: str = "knowledge_base"):
    steps = next_steps([entry], call, tool=tool)
    assert steps is not None
    return steps[0]


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (
            {"tool": "knowledge_base", "command": "rows", "arguments": {"offset": 3}},
            "beakr kb rows --offset 3",
        ),
        (
            {
                "tool": "knowledge_base_write",
                "command": "add_fact",
                "arguments": {"page": "Acme", "attribute": "HQ", "value": "Boston"},
            },
            "beakr kb add-fact Acme --attribute HQ --value Boston --rationale <rationale>",
        ),
        (
            {
                "tool": "knowledge_base_write",
                "command": "add_connection",
                "arguments": {"page": "<page>", "relation": "works at", "to": "Acme"},
            },
            "beakr kb add-connection <page> --relation 'works at' --to Acme "
            "--rationale <rationale>",
        ),
        (
            {"tool": "knowledge_base_write", "command": "new", "arguments": {"title": "<title>"}},
            "beakr kb propose create --title <title>",
        ),
        (
            # An action `beakr kb` has no command for: the write door's own (prints JSON).
            {
                "tool": "knowledge_base_write",
                "command": "edit_section",
                "arguments": {"page": "<page>", "section_id": "<section_id>"},
            },
            "beakr api knowledge run-wiki-write --action edit_section --page <page> "
            "--section-id <section_id>",
        ),
        (
            {"tool": "documents", "command": "grep", "arguments": {"pattern": "NW-217"}},
            "beakr documents grep NW-217",
        ),
        (
            {"tool": "read_file", "command": None, "arguments": {"artifact_id": "<file_id>"}},
            "beakr documents read <file_id>",
        ),
        (
            {"tool": "integrations", "command": "list", "arguments": {"provider": "slack"}},
            "beakr integrations tools --provider slack",
        ),
        (
            {
                "tool": "integrations",
                "command": "call",
                "arguments": {"tool": "granola_get_note", "arguments": {"note_id": "not_1"}},
            },
            'beakr integrations call granola_get_note --args \'{"note_id": "not_1"}\'',
        ),
        (
            {"tool": "research", "command": None, "arguments": {"query": "Who owns billing?"}},
            "beakr research 'Who owns billing?'",
        ),
        (
            {"tool": "show_proposal", "command": None, "arguments": {"proposal_id": PROPOSAL}},
            f"beakr kb proposals show {PROPOSAL}",
        ),
        (
            {"tool": "accept_proposal", "command": None, "arguments": {"proposal_id": PROPOSAL}},
            f"beakr kb proposals accept {PROPOSAL}",
        ),
        ({"tool": "list_projects", "command": None, "arguments": {}}, "beakr projects ls"),
    ],
    ids=lambda value: value if isinstance(value, str) else value["tool"],
)
def test_each_tool_becomes_its_beakr_command(entry: dict[str, Any], expected: str) -> None:
    step = _one({**entry, "why": "Because."})

    assert step.command == expected
    parse_command(step.command)


@pytest.mark.parametrize(
    "entry",
    [
        # A tool no `beakr` command makes.
        {"tool": "upload_files", "command": None, "arguments": {"paths": ["a.txt"]}},
        {"tool": "send_email", "command": None, "arguments": {}},
        # An argument the command has no option for: dropping it would read something else.
        {"tool": "documents", "command": "read", "arguments": {"document": "x", "frobnicate": 1}},
        {"tool": "read_file", "command": None, "arguments": {"artifact_id": "x", "offset": 600}},
        # Not a write action the door has.
        {"tool": "knowledge_base_write", "command": "drop_table", "arguments": {}},
        # A proposal id that is not an id.
        {"tool": "accept_proposal", "command": None, "arguments": {"proposal_id": "--yes"}},
        # A provider tool name that is not a name.
        {"tool": "integrations", "command": "call", "arguments": {"tool": "x; rm -rf ~"}},
    ],
    ids=lambda entry: f"{entry['tool']}-{entry.get('command')}",
)
def test_a_step_no_command_makes_whole_prints_only_its_why(entry: dict[str, Any]) -> None:
    step = _one({**entry, "why": "Do the thing."})

    assert step.command is None
    assert next_block([step]) == ["Next:", "  - Do the thing."]


def test_next_calls_that_are_not_a_list_of_steps_fall_back_to_the_prose() -> None:
    for value in (None, [], "cat {page}", [{"command": "cat"}], [["knowledge_base"]]):
        assert next_steps(value) is None


def test_the_staged_proposal_fills_an_accept_step_that_names_none() -> None:
    step = _one(
        {"tool": "accept_proposal", "command": None, "arguments": {}, "why": "Accept it."},
        DoorCall("add_fact", proposal_id=PROPOSAL),
        tool="knowledge_base_write",
    )

    assert step.command == f"beakr kb proposals accept {PROPOSAL}"


# ---------------------------------------------------------------------------
# Hostile content through the structured path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", HOSTILE, ids=IDS)
def test_hostile_values_in_next_calls_print_only_safe_commands(
    hostile: str, shells: Shells
) -> None:
    entries = [
        {
            "tool": "knowledge_base",
            "command": "cat",
            "arguments": {"page": hostile},
            "why": hostile,
        },
        {
            "tool": "knowledge_base",
            "command": "grep",
            "arguments": {"query": hostile, "after_title": hostile},
            "why": "Read on.",
        },
        {
            "tool": "integrations",
            "command": "call",
            "arguments": {"tool": "github_get_file", "arguments": {"path": hostile}},
            "why": "Open it.",
        },
        {
            "tool": "knowledge_base_write",
            "command": "add_finding",
            "arguments": {"page": hostile, "text": hostile},
            "why": "Save it.",
        },
        {"tool": "documents", "command": "read", "arguments": {"document": hostile}, "why": "x"},
        # A hostile tool or command name is never a command.
        {"tool": hostile, "command": "cat", "arguments": {}, "why": "x"},
        {"tool": "knowledge_base", "command": hostile, "arguments": {}, "why": "x"},
    ]
    body = {"content": "listing", "next": ["Open one with cat {page}."], "next_calls": entries}
    call = DoorCall("grep", {"query": "x", "scope": PROJECT})

    output = visible(shell_reply("knowledge_base", body, call))

    # (c) nothing a terminal hides reaches the output raw.
    assert not unprintable(output.replace("\n", "")), repr(output)
    block = _block(output)
    commands = _commands(block)
    if unprintable(hostile):
        # No command is built from a value a terminal would not show as it is.
        assert commands == []
    else:
        assert len(commands) == 5, block
        for command in commands:
            parse_command(command)  # (a)
            shells.check(command)  # (a) and (b): exactly these words, nothing else runs
    # (d) a why is printed as text: no line of it is a command.
    for line in block:
        if not line.startswith("  beakr "):
            assert not line.strip().startswith("beakr "), line
