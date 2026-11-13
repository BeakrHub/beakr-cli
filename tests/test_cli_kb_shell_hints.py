"""``beakr kb`` prints each next step as a ``beakr`` command, not as an MCP call.

ROOT CAUSE (live check 2026-11-13, captures under ``cli/``): the Knowledge door
names the next call the way an MCP client makes it, and ``beakr kb`` printed its
text unchanged. ``kb cat`` ended ``cat {page: "...", lines: "5-533"}``, ``kb
proposals ls --limit 1`` ended ``proposals {... "offset": 1}``, ``kb
add-finding`` said ``call accept_proposal``: none of them runs in a shell.

``fixtures/kb_live_door_replies.json`` holds the replies of that run. Every
command these tests find in a translated reply is parsed by Click against the
real command tree (``tests/_shell_commands.py``), so a hint naming a flag the
CLI does not have fails here.
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

from beakr_cli.kb_shell_commands import COMMANDS
from beakr_cli.kb_shell_hints import (
    DoorCall,
    Slot,
    shell_command,
    shell_content,
    shell_hint,
    shell_reply,
    with_cursor,
)
from beakr_cli.main import internal_app as app
from beakr_cli.mcp_render import render
from tests._shell_commands import parse_command, printed_commands

API = "https://api.test"
PROJECT = "bfe240a3-c424-429f-b120-eba95dd60260"
PAGE = "caf937ee-d99b-4b5e-8e62-44f15bf084ed"
PROPOSAL = "3d145cf5-734f-4a08-8b4c-07a3d9432482"
LIVE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "kb_live_door_replies.json").read_text()
)
TEXT = LIVE["text"]
runner = CliRunner()

#: A door call as an MCP client writes it: nothing a shell can run.
MCP_CALL = re.compile(
    r"(?<![\w`.-])(?:cat|ls|grep|sections|sources|hover|links|timeline|provenance|log|"
    r"proposals|rows|query|diff|show|completions) \{"
    r"|\b(?:show|accept|dismiss)_proposal\b|\bread_file\b|\bknowledge_base\b"
    r"|integrations command=|again with offset="
)


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _call(capture: dict[str, Any]) -> DoorCall:
    return DoorCall(capture["command"], capture["arguments"], capture.get("proposal_id"))


def _translated(capture: dict[str, Any]) -> list[str]:
    call = _call(capture)
    write = capture["command"] == "add_finding"
    return [
        shell_content(capture["content"], call, acknowledgement=write),
        *(shell_hint(hint, call) for hint in capture["next"]),
    ]


# ---------------------------------------------------------------------------
# The replies of the live run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(TEXT))
def test_no_next_hint_of_the_live_run_is_left_in_mcp_syntax(name: str) -> None:
    capture = TEXT[name]
    assert any(MCP_CALL.search(hint) for hint in capture["next"]) or name in (
        "add_finding",
        "sources_door",
    ), "the capture no longer shows the defect"

    for hint in capture["next"]:
        assert not MCP_CALL.search(shell_hint(hint, _call(capture))), hint


@pytest.mark.parametrize("name", sorted(TEXT))
def test_every_command_a_live_reply_prints_is_a_real_beakr_command(name: str) -> None:
    commands = [command for text in _translated(TEXT[name]) for command in printed_commands(text)]

    assert commands or name == "sources_door"
    for command in commands:
        parse_command(command)


def test_a_cut_page_names_the_command_that_continues_it() -> None:
    hints = [shell_hint(hint, _call(TEXT["cat_cut"])) for hint in TEXT["cat_cut"]["next"]]

    assert f"Continue exactly where this stops: `beakr kb cat {PAGE} --lines 5-533`." in hints[0]
    assert f"Read one with `beakr kb cat {PAGE} --section-id sec_rules`." in hints[0]
    assert parse_command(f"beakr kb cat {PAGE} --lines 5-533") == (
        "kb cat",
        {"page": PAGE, "lines": "5-533"},
    )


def test_a_cut_proposal_listing_names_offset_as_a_flag() -> None:
    capture = TEXT["proposals_limit1"]
    content = shell_content(capture["content"], _call(capture))
    hints = [shell_hint(hint, _call(capture)) for hint in capture["next"]]

    expected = (
        f"beakr kb proposals ls --status accepted --project {PROJECT} --limit 1 --offset 1 --by me"
    )
    assert f"continue with `{expected}`." in content.splitlines()[-1]
    assert parse_command(expected) == (
        "kb proposals ls",
        {"status_filter": "accepted", "project": PROJECT, "limit": 1, "offset": 1, "by": "me"},
    )
    # "call proposals again with offset=1" is the same listing, whole.
    again = printed_commands(hints[0])
    assert len(again) == 1
    assert parse_command(again[0]) == parse_command(expected)
    assert hints[1] == (
        "Review one with `beakr kb proposals show <proposal_id>`, then "
        "`beakr kb proposals accept <proposal_id>` or `beakr kb proposals dismiss <proposal_id>`."
    )


def test_a_staged_write_names_the_accept_command_with_its_proposal() -> None:
    capture = TEXT["add_finding"]
    text = shell_content(capture["content"], _call(capture), acknowledgement=True)

    assert (
        f"run `beakr kb proposals accept {PROPOSAL}` only when the user asks you to accept "
        "this proposal."
    ) in text
    assert "accept_proposal" not in text


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("hover", ["beakr kb ls --parent <parent>", "beakr kb links <page> --direction out"]),
        ("links", ["beakr kb cat <page>"]),
        ("timeline", ["beakr kb cat <page>", "beakr kb timeline <page>"]),
        ("provenance", ["beakr kb sources <page>"]),
        ("sections", ["beakr kb cat <page> --section-id <section_id>", "beakr kb hover <page>"]),
        ("grep_hit", ["beakr kb cat <page> [--section-id <section_id>]"]),
    ],
)
def test_graph_and_search_hints_name_the_kb_commands(name: str, expected: list[str]) -> None:
    hints = " ".join(shell_hint(hint, _call(TEXT[name])) for hint in TEXT[name]["next"])

    for command in expected:
        assert f"`{command}`" in hints


def test_the_doors_other_spelling_of_a_call_is_translated_in_a_listing_trailer() -> None:
    # grep's trailer says `cat <page> section_id=<id>`, in backticks.
    text = shell_content(TEXT["grep_hit"]["content"], _call(TEXT["grep_hit"]))

    assert "Read a hit with `beakr kb cat <page> --section-id <id>`." in text
    assert "Use `beakr kb sections <query>` to search by meaning." in text
    assert (
        "by title prefix with `beakr api knowledge run-wiki-command --command completions "
        "--prefix <prefix>`."
    ) in text


def test_a_connected_source_line_names_the_integrations_command() -> None:
    capture = TEXT["sources_door"]
    text = shell_content(capture["content"], _call(capture))

    fetch = [line.strip() for line in text.splitlines() if line.strip().startswith("fetch: ")]
    assert fetch == [
        "fetch: beakr integrations call slack_get_thread --arg channel_id=C04K10YTMN0 "
        "--arg thread_ts=1742744865.524899",
        "fetch: beakr integrations call granola_get_note --arg note_id=not_LiveRf00000001",
    ]
    for line in fetch:
        path, given = parse_command(line.removeprefix("fetch: "))
        assert path == "integrations call" and given["arg"]
    # The same call as the engine prints it for an outside caller, and as a Sources line.
    outside = (
        "     fetch: integrations command='call' tool='granola_get_note' "
        'arguments={"note_id": "not_LiveRf00000001"}'
    )
    assert shell_content(outside, _call(capture)) == (
        "     fetch: beakr integrations call granola_get_note "
        '--args \'{"note_id": "not_LiveRf00000001"}\''
    )


# ---------------------------------------------------------------------------
# A door call as a command
# ---------------------------------------------------------------------------


def _sample(key: str) -> Any:
    numbers = ("limit", "offset", "rev", "source_offset", "context_lines")
    return 7 if key in numbers else {"direction": "out"}.get(key, f"{key} value")


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_option_of_every_kb_command_becomes_its_flag(command: str) -> None:
    spec = COMMANDS[command]
    arguments: dict[str, Any] = {key: _sample(key) for key in spec.options}
    if spec.positional:
        arguments[spec.positional] = "Compound NW-214"
    arguments |= {key: value for (key, value), flag in spec.switches.items() if flag}
    arguments["scope"] = PROJECT

    printed = shell_command(command, arguments)
    path, given = parse_command(printed)

    assert printed.startswith(f"beakr {spec.path} "), printed
    assert path == spec.path
    assert given.pop("project", given.pop("scope", None)) == PROJECT
    # Nothing of the call was dropped: every argument landed on a parameter.
    assert len(given) == len(arguments) - 1, (printed, given)


@pytest.mark.parametrize(
    ("command", "arguments", "expected"),
    [
        # `kb links` has no page_type flag, so the whole call is the door's own command.
        (
            "links",
            {"page": Slot("page"), "page_type": Slot("page_type")},
            "beakr api knowledge run-wiki-command --command links --page <page> "
            "--page-type <page_type>",
        ),
        (
            "diff",
            {"page": "Assay", "rev": 2, "rev2": Slot("rev2", optional=True)},
            "beakr api knowledge run-wiki-command --command diff --page Assay --rev 2 "
            "[--rev2 <rev2>]",
        ),
        # `kb ls --after-title` continues only an author-filtered listing.
        (
            "ls",
            {"after_title": "Matter 12", "scope": PROJECT},
            "beakr api knowledge run-wiki-command --command ls --after-title 'Matter 12' "
            f"--project {PROJECT}",
        ),
        (
            "sources",
            {"page": "Assay", "after_title": "memo.pdf", "after_source_id": "s-1"},
            "beakr api knowledge run-wiki-command --command sources --page Assay "
            "--after-title memo.pdf --after-source-id s-1",
        ),
        (
            "query",
            {"spec": {"from": "facts"}},
            'beakr api knowledge run-wiki-command --data \'{"command": "query", '
            '"spec": {"from": "facts"}}\'',
        ),
    ],
)
def test_a_call_without_a_dedicated_flag_falls_back_to_the_door_command(
    command: str, arguments: dict[str, Any], expected: str
) -> None:
    printed = shell_command(command, arguments)

    assert printed == expected
    path, _ = parse_command(printed)
    assert path == "api knowledge run-wiki-command"


def test_values_are_quoted_for_the_shell() -> None:
    assert shell_command("cat", {"page": "Helios Operating Handbook"}) == (
        "beakr kb cat 'Helios Operating Handbook'"
    )
    assert shell_command("grep", {"query": "-x it's"}) == "beakr kb grep -- '-x it'\"'\"'s'"
    assert parse_command(shell_command("grep", {"query": "-x it's"})) == (
        "kb grep",
        {"pattern": "-x it's"},
    )


@pytest.mark.parametrize(
    ("hint", "call", "expected"),
    [
        (
            "More pages link here: continue with offset=40.",
            DoorCall("links", {"page": "Project Helios", "scope": PROJECT}),
            f"continue with `beakr kb links 'Project Helios' --project {PROJECT} --offset 40`.",
        ),
        (
            "This list is NOT complete: continue with the same filters and "
            'after_title="Matter 12".',
            DoorCall("ls", {"changed_by": "me", "limit": 3}),
            "continue with `beakr kb ls --changed-by me --limit 3 --after-title 'Matter 12'`.",
        ),
        (
            "Later events remain. Continue with cursor=abc123 and the same filters.",
            DoorCall("timeline", {"page_type": "compound"}),
            "Continue with `beakr kb timeline --type compound --cursor abc123`.",
        ),
        (
            "More rows: call rows again with offset=150.",
            DoorCall("rows", {"table": "facts", "attribute": "Status", "match": "exact"}),
            "run `beakr kb rows --table facts --attribute Status --match exact --offset 150`.",
        ),
    ],
)
def test_a_continuation_is_the_whole_command_run_again(
    hint: str, call: DoorCall, expected: str
) -> None:
    translated = shell_hint(hint, call)

    assert translated.endswith(expected), translated
    for command in printed_commands(translated):
        parse_command(command)


def test_a_continuation_repeats_a_flag_the_door_call_does_not_carry() -> None:
    # `kb ls --all` reads every project by sending no scope; without --all the
    # continued listing would read the bound project instead.
    call = DoorCall("ls", {"changed_by": "me", "scope": None}, flags=("--all",))
    hint = 'This list is NOT complete: continue with the same filters and after_title="M 12".'

    translated = shell_hint(hint, call)

    assert translated.endswith("`beakr kb ls --changed-by me --after-title 'M 12' --all`.")
    assert parse_command(printed_commands(translated)[0])[1]["all_scopes"] is True


# ---------------------------------------------------------------------------
# What must not change
# ---------------------------------------------------------------------------


def test_quoted_page_text_is_never_rewritten() -> None:
    page = "# Agent rules\n\nRead with cat {page} and call accept_proposal.\n\nLast line."
    middle = "3 hits\n\n1. Rules\n   Use cat {page} and read_file.\n\nRead a hit with cat {page}."

    assert shell_content(page, DoorCall("cat", {"page": "Agent rules"})) == page
    listing = shell_content(middle, DoorCall("sections", {"query": "rules"}))
    assert "   Use cat {page} and read_file." in listing
    assert listing.endswith("Read a hit with `beakr kb cat <page>`.")


def test_the_stdio_mcp_reply_keeps_the_mcp_form() -> None:
    reply = LIVE["json"]["cat"]["reply"]
    body = {
        "content": "\n".join(block["text"] for block in reply["content"]),
        "coverage": reply.get("coverage"),
        "next": reply["next"],
    }

    assert "links {page, page_type} or {page, direction: out}" in render("knowledge_base", body)
    shown = shell_reply("knowledge_base", body, DoorCall("cat", {"page": "Compound NW-214"}))
    assert "links {page" not in shown
    assert "`beakr kb links <page> --direction out`" in shown


@respx.mock
def test_kb_cat_prints_commands_and_json_keeps_the_doors_reply() -> None:
    cut = TEXT["cat_cut"]
    page = {"id": PAGE, "title": "Helios Operating Handbook", "project_id": PROJECT}
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE}").mock(return_value=Response(200, json=page))
    reply = {
        "content": [{"type": "text", "text": cut["content"]}],
        "outcome": {"status": "success"},
        "coverage": {"complete": False},
        "next": cut["next"],
        "sources": [],
    }
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=Response(200, json=reply))

    text = runner.invoke(app, ["kb", "cat", PAGE])
    as_json = runner.invoke(app, ["kb", "cat", PAGE, "--json"])

    assert text.exit_code == 0, text.output
    assert f"Continue exactly where this stops: `beakr kb cat {PAGE} --lines 5-533`." in text.stdout
    assert not MCP_CALL.search(
        text.stdout.split("Coverage: ")[0] + text.stdout.split("next: ", 1)[1]
    )
    assert json.loads(as_json.stdout) == reply


@respx.mock
def test_kb_sections_prints_the_command_that_reads_a_cited_note() -> None:
    # The door's reply carries the note's read call (`retrieval`); the Sources
    # line named no way to read it, in MCP form or any other.
    reply = LIVE["json"]["sections"]["reply"]
    assert any(source.get("retrieval") for source in reply["sources"])
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=Response(200, json=reply))

    result = runner.invoke(app, ["kb", "sections", "Helios stability", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    line = next(row for row in result.stdout.splitlines() if row.startswith("- Granola: Helios"))
    command = line.split(" · read with ", 1)[1]
    assert parse_command(command) == (
        "integrations call",
        {"tool": "granola_get_note", "args_json": '{"note_id": "not_LiveRf00000001"}'},
    )
    assert "read_file" not in result.stdout


@respx.mock
def test_kb_add_finding_names_the_accept_command() -> None:
    capture = TEXT["add_finding"]
    content = capture["content"].rsplit("\ncontent_hash: ", 1)[0]
    reply = {
        "content": [{"type": "text", "text": content}],
        "outcome": {"status": "success"},
        "proposal": {"id": PROPOSAL, "content_hash": "15e3"},
    }
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=Response(200, json=reply))
    arguments = ["kb", "add-finding", "Compound NW-250", "--date", "2026-11-13", "--kind", "lesson"]
    arguments += ["--text", "Dropped compounds keep their card", "--rationale", "Live check."]
    arguments += ["--evidence", "(stability-protocol.md › block 5)", "--project", PROJECT]

    text = runner.invoke(app, arguments)
    as_json = runner.invoke(app, [*arguments, "--json"])

    assert text.exit_code == 0, text.output
    assert f"run `beakr kb proposals accept {PROPOSAL}` only when the user asks" in text.stdout
    assert "content_hash: 15e3" in text.stdout
    assert json.loads(as_json.stdout) == reply


#: Page titles a reply may carry into a command. The first is the title of the
#: live rerun (2026-11-13); the rest are one hazard each.
HOSTILE_TITLES = [
    'Quote\'s "edge" $(echo PWNED2) `echo PWNED3`',
    'say "hi"',
    '"',
    "it's",
    "tick `id`",
    "$(touch pwned)",
    "a; rm -rf ~",
    "line one\nline two",
    "-rf --all",
    "--project=other",
    "C:\\new\\table",
    "Café › Übersicht",
]


def _shell_words(printed: str, cwd: Path) -> list[str]:
    """The arguments a POSIX shell passes to ``beakr`` for ``printed``, run in ``cwd``."""
    assert printed.startswith("beakr ")
    script = f'set -- {printed.removeprefix("beakr ")}\nprintf "%s\\0" "$@"'
    done = subprocess.run(["sh", "-c", script], capture_output=True, check=True, cwd=cwd)
    assert not done.stderr, done.stderr
    return done.stdout.decode().split("\0")[:-1]


def test_a_name_from_a_reply_never_reaches_the_shell_unquoted(tmp_path):
    """A placeholder or argument name is reply text: only a plain identifier prints bare.

    Root cause: a slot printed as ``<name>`` whatever the name held, and an
    unknown argument name became a ``--flag`` verbatim, so a reply naming
    ``<x; rm -rf ~>`` or an argument ``a; curl evil`` produced a pasteable
    command that ran it.

    A value is reply text too (a page title). It reaches the shell as one
    quoted word, and Click as the value of its option: one that opens with a
    dash is joined to its flag (``--after-title=-rf``) or follows ``--``, so it
    is never read as an option (live rerun 2026-11-13).
    """
    hostile = "x; rm -rf ~"
    for printed in (
        shell_command("cat", {"page": Slot(hostile)}),
        shell_command("cat", {"page": "p", f"lines; {hostile}": "1"}),
        shell_command(f"cat; {hostile}", {"page": "p"}),
    ):
        words = shlex.split(printed)
        assert words[0] == "beakr"
        # Nothing hostile is a separate shell word or operator once the line is parsed.
        assert ";" not in words and "rm" not in words, printed
    assert str(Slot("page")) == "<page>"

    # Where the shell runs each command: anything a title ran would leave a file here.
    scratch = tmp_path / "shell"
    scratch.mkdir()
    for title in HOSTILE_TITLES:
        calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
            # (door command, arguments, what Click must read back: every option
            # keeps its own value, none is swallowed by the title before it)
            ("ls", {"changed_by": "me", "after_title": title}, {"after_title": title}),
            (
                "grep",
                {"query": title, "after_title": title, "limit": 3},
                {"pattern": title, "after_title": title, "limit": 3},
            ),
            ("cat", {"page": title, "lines": title}, {"page": title, "lines": title}),
            ("rows", {"entity": title, "query": title}, {"entity": title, "query": title}),
            ("log", {"page": title, "by": title}, {"page": title, "by": title}),
            (
                "proposals",
                {"by": title, "status": "pending"},
                {"by": title, "status_filter": "pending"},
            ),
            # The generic door command, as options and as a JSON body.
            ("ls", {"after_title": title}, {"b__after_title": title}),
            (
                "sources",
                {"page": title, "after_title": title},
                {"b__page": title, "b__after_title": title},
            ),
        ]
        for command, arguments, expected in calls:
            printed = shell_command(command, arguments)
            _, given = parse_command(printed)
            assert {key: given.get(key) for key in expected} == expected, printed
            if shutil.which("sh"):
                assert _shell_words(printed, scratch) == shlex.split(printed)[1:], printed
        body = shell_command("ls", {"after_title": title, "after_page_id": PAGE})
        _, given = parse_command(body)
        assert json.loads(given["data"])["after_title"] == title, body
        if shutil.which("sh"):
            assert _shell_words(body, scratch) == shlex.split(body)[1:], body
    # Nothing a title held ran: no file, no deletion.
    assert list(scratch.iterdir()) == []


# ---------------------------------------------------------------------------
# A continuation whose cursor is a title
# ---------------------------------------------------------------------------

_LS = {"page_type": "topic", "limit": 3, "changed_since": "2026-11-12", "scope": PROJECT}


def _ls_hint(printed_title: str) -> str:
    return (
        "Not everything is shown: call ls again with the same filters and "
        f"after_title={printed_title} to read the rest."
    )


def _spelled(form: str, title: str) -> str:
    # `raw` is how the door printed a title until it escaped it; `json` is how it does now.
    return f'"{title}"' if form == "raw" else json.dumps(title, ensure_ascii=form == "ascii")


@pytest.mark.parametrize("form", ["raw", "json", "ascii"])
@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_a_continuation_is_built_from_the_replys_cursor_not_its_prose(
    title: str, form: str
) -> None:
    """ROOT CAUSE (live rerun 2026-11-13): the door printed ``after_title="Quote's
    "edge" $(echo PWNED2) ..."`` with the title's quotes unescaped. The hint
    was cut at the first inner quote (``--after-title 'Quote'"'"'s '``), the
    rest of the title was printed outside the command, and pasting it listed
    the same page again. The reply's ``coverage.continue_with`` held the title
    whole all along.
    """
    call = with_cursor(DoorCall("ls", _LS), {"continue_with": {"after_title": title}})

    translated = shell_hint(_ls_hint(_spelled(form, title)), call)

    expected = shell_command("ls", {**_LS, "after_title": title})
    assert translated == f"Not everything is shown: run `{expected}` to read the rest."
    path, given = parse_command(expected)
    assert path == "kb ls"
    assert given["after_title"] == title
    assert given["page_type"] == "topic" and given["project"] == PROJECT


@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_a_json_string_in_prose_is_read_with_its_escapes(title: str) -> None:
    # No structured cursor (an older server, a hint outside the reply's own listing).
    translated = shell_hint(_ls_hint(json.dumps(title, ensure_ascii=False)), DoorCall("ls", _LS))

    if "\n" in title:
        # `\\n` is also what a raw `C:\\new` looks like: not read, so not guessed.
        assert translated == _ls_hint(json.dumps(title, ensure_ascii=False))
        return
    expected = shell_command("ls", {**_LS, "after_title": title})
    assert translated == f"Not everything is shown: run `{expected}` to read the rest."
    assert parse_command(expected)[1]["after_title"] == title


@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_a_value_prose_cannot_carry_is_left_as_the_door_wrote_it(title: str) -> None:
    hint = _ls_hint(f'"{title}"')

    translated = shell_hint(hint, DoorCall("ls", _LS))

    if json.dumps(title, ensure_ascii=False) == f'"{title}"':
        # Nothing in the title needed escaping: the raw form is the JSON form.
        expected = shell_command("ls", {**_LS, "after_title": title})
        assert translated == f"Not everything is shown: run `{expected}` to read the rest."
    else:
        assert translated == hint
        assert not printed_commands(translated)


@pytest.mark.parametrize(
    "hint",
    [
        # The title's own quote ends the value early, then the page id follows:
        # the id alone is another place in the listing.
        'This list is NOT complete: continue with after_title="A "b" c" after_page_id="3d93".',
        'This list is NOT complete: continue with after_title="A" b c" after_page_id="3d93".',
        # Python's repr of a title: where it ends is not certain.
        "No matching pages after after_title='Foo Bar': all 3 are listed above.",
        # The same argument twice.
        'Continue with after_title="A" and after_title="B".',
    ],
)
def test_an_ambiguous_cursor_in_prose_prints_no_command(hint: str) -> None:
    assert shell_hint(hint, DoorCall("grep", {"query": "x"})) == hint


def test_an_unreadable_cursor_in_prose_is_followed_by_the_replys_own() -> None:
    # The prose cannot be read and does not spell the cursor the reply carries:
    # it stays, and the command built from the reply's data follows it.
    hint = 'This list is NOT complete: continue with after_title="A" b c" after_page_id="3d93".'
    cursor = {"after_title": 'Another "title"', "after_page_id": "3d93"}
    call = with_cursor(DoorCall("grep", {"query": "x"}), {"continue_with": cursor})

    translated = shell_hint(hint, call)

    expected = shell_command("grep", {"query": "x", **cursor})
    assert translated == f"{hint} Continue with `{expected}`."
    assert parse_command(expected)[1]["after_title"] == 'Another "title"'


def test_a_marker_character_in_a_reply_is_not_read_as_a_marker() -> None:
    # The translation marks kept commands and pinned values with control
    # characters; a reply carrying them must not index a marker that is not there.
    hint = "Continue with after_title=\x017\x01 or see \x003\x00, then cat {page}."

    assert shell_hint(hint, DoorCall("ls", {"changed_by": "me"})) == (
        "Continue with `beakr kb ls --changed-by me --after-title 7` or see 3, "
        "then `beakr kb cat <page>`."
    )


def test_a_placeholder_call_with_an_unreadable_value_is_left_alone() -> None:
    hint = 'Read on with cat {page: "A "b" c", lines: "5-9"}.'

    assert shell_hint(hint, DoorCall("cat", {"page": "x"})) == hint


def test_the_doors_own_spelling_of_a_continuation_repeats_the_cli_flags() -> None:
    # `kb ls --all` prints "continue with ls {json}": built from the door call
    # alone it lost --all and would have listed the bound project instead.
    cursor = {"after_title": 'Quote\'s "edge"', "after_page_id": PAGE}
    resend = {"limit": 3, **cursor, "changed_by": "me"}
    call = with_cursor(
        DoorCall("ls", {"changed_by": "me", "limit": 3, "scope": None}, flags=("--all",)),
        {"continue_with": cursor},
    )

    translated = shell_hint(f"NOT complete: continue with ls {json.dumps(resend)}.", call)

    (command,) = printed_commands(translated)
    path, given = parse_command(command)
    assert path == "kb ls"
    assert given == {"changed_by": "me", "limit": 3, **cursor, "all_scopes": True}


@pytest.mark.parametrize(
    ("command", "coverage", "hint", "expected"),
    [
        (
            "log",
            {"next_offset": 40},
            "Not everything is shown: call log again with offset=40 to read the rest.",
            "beakr kb log --by me --offset 40",
        ),
        (
            "rows",
            {"next_cursor": "c0522cf7-d82a-4d2a-89b3-16819eecc5bc"},
            "Later candidates remain: call rows with "
            "after_card=c0522cf7-d82a-4d2a-89b3-16819eecc5bc and the same filters.",
            "beakr kb rows --by me --after-card c0522cf7-d82a-4d2a-89b3-16819eecc5bc",
        ),
    ],
)
def test_next_offset_and_next_cursor_continue_the_call_that_was_made(
    command: str, coverage: dict[str, Any], hint: str, expected: str
) -> None:
    arguments = {"by": "me"} if command == "log" else {"table": "facts"}
    call = with_cursor(DoorCall(command, arguments), {"complete": False, **coverage})

    translated = shell_hint(hint, call)

    expected = expected.replace("rows --by me", "rows --table facts")
    assert f"run `{expected}`" in translated
    assert "again with" not in translated and "call rows" not in translated
    parse_command(expected)


def test_a_positional_that_opens_with_a_dash_goes_after_every_option() -> None:
    # After `--` everything is positional: an option printed after it was lost.
    printed = shell_command(
        "grep", {"query": "-rf", "limit": 3, "after_title": "-x", "scope": PROJECT}, flags=()
    )

    assert printed == f"beakr kb grep --limit 3 --after-title=-x --project {PROJECT} -- -rf"
    assert parse_command(printed) == (
        "kb grep",
        {"pattern": "-rf", "limit": 3, "after_title": "-x", "project": PROJECT},
    )


# ---------------------------------------------------------------------------
# Every text path of `beakr kb` that prints the door's words
# ---------------------------------------------------------------------------

_CORPUS_NOTICE = (
    "Knowledge was compiled from 0 of 17 file(s) in this project (17 not compiled yet). "
    "This search covers what was compiled, not every file: a match may still be in a file "
    "that is missing or partial here."
)


def test_the_sections_footer_is_translated_when_the_corpus_notice_follows_it() -> None:
    """ROOT CAUSE (live rerun 2026-11-13): ``kb sections`` printed "Read a hit
    with cat {page, section_id}: ... sources {page} ... documents read (read_file
    in MCP)" unchanged while ``kb grep`` translated the same sentence. Only a
    reply's first and last paragraphs are translated, and the door now closes a
    search with how much of the project was compiled, after its footer: the
    footer was no longer the last paragraph.
    """
    capture = TEXT["sections"]
    footer = capture["content"].rsplit("\n\n", 1)[1]
    assert footer.startswith("Read a hit with cat {page, section_id}:")
    content = f"{capture['content']}\n\n{_CORPUS_NOTICE}"

    text = shell_content(content, _call(capture))

    closing = text.split("\n\n")[-2:]
    assert closing[0].startswith(
        "Read a hit with `beakr kb cat <page> --section-id <section_id>`: it prints"
    )
    assert "`beakr kb sources <page>` lists every passage a page cites" in closing[0]
    assert "`beakr documents read <document>` with a path above" in closing[0]
    assert not MCP_CALL.search(closing[0])
    assert closing[1] == _CORPUS_NOTICE
    # The hits between the opening and the footer are the organization's text.
    middle = "\n\n".join(content.split("\n\n")[1:-2])
    assert middle and middle in text


_DOOR_TEXT = (
    "3 result(s):\n\n"
    "  - [topic] Agent rules -- Use cat {page} and call accept_proposal.\n\n"
    "Read a hit with cat {page, section_id}. sources {page} lists what a page cites.\n\n"
    f"{_CORPUS_NOTICE}"
)


@pytest.mark.parametrize(
    "arguments",
    [
        ["sections", "rules"],
        ["grep", "rules"],
        ["ls", "--changed-by", "me"],
        ["log"],
        ["log", "--by", "me"],
        ["log", PAGE, "--by", "me"],
        ["history", "--by", "me"],
        ["hover", "Agent rules"],
        ["provenance", "Agent rules"],
        ["links", "Agent rules"],
        ["timeline"],
        ["proposals", "ls", "--by", "me"],
        ["rows", "--table", "facts"],
    ],
    ids=lambda arguments: " ".join(arguments[:2]),
)
@respx.mock
def test_every_kb_command_that_prints_the_doors_text_translates_it(arguments: list[str]) -> None:
    page = {"id": PAGE, "title": "Agent rules", "project_id": PROJECT}
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE}").mock(return_value=Response(200, json=page))
    reply = {
        "content": [{"type": "text", "text": _DOOR_TEXT}],
        "outcome": {"status": "success"},
        "coverage": {"complete": True},
        "next": ["Open a hit with cat {page, section_id}."],
        "sources": [],
    }
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=Response(200, json=reply))

    result = runner.invoke(app, ["kb", *arguments, "--project", PROJECT])

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Read a hit with `beakr kb cat <page> --section-id <section_id>`." in out
    assert "`beakr kb sources <page>` lists what a page cites." in out
    # The organization's own text is quoted as it is.
    assert "Use cat {page} and call accept_proposal." in out
    if arguments[0] != "rows":
        # `kb rows` prints the reply's text only, not its `next` steps.
        assert "next: Open a hit with `beakr kb cat <page> --section-id <section_id>`." in out
    for command in printed_commands(out):
        parse_command(command)
