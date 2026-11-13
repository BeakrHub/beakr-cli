"""Every door call ``beakr kb`` can be asked to print is a command that runs.

ROOT CAUSE (live rerun 2026-11-13, capture ``cli/p_0NN``): ``beakr kb ls
--changed-since 2026-11-12 --type topic --limit 1 --after-title ...`` ended
"continue with ``beakr api knowledge run-wiki-command --command ls ...
--after-page-id 3d9356d4-...``", and pasting it exits 2 with "No such option:
--after-page-id". Two gaps made it:

- ``kb ls`` had no ``--after-page-id`` (``kb grep`` did) and the ``ls`` spelling
  in ``kb_shell_commands.COMMANDS`` did not map ``after_page_id``, so the call
  fell back to the generic door command;
- the generic command was assumed to take every argument of every door command.
  It is generated from the API contract this CLI was built with
  (``data/operations.json``), which has no ``after_page_id``: an argument was
  turned into ``--its-name`` whether or not that option exists.

These tests close the class: every argument the door takes, for every command,
either has a flag on the dedicated ``beakr kb`` command or is listed in
``GENERIC_ONLY``; and whatever is printed, dedicated or generic, is parsed by
Click against the real command tree and must carry the argument.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

from beakr_cli.commands.api import operations
from beakr_cli.kb_shell_commands import COMMANDS, DOOR_COMMANDS, GENERIC_ONLY, shell_command
from beakr_cli.kb_shell_hints import DoorCall, shell_reply
from beakr_cli.main import internal_app as app
from tests._engine import _candidates, _missing, is_snapshot
from tests._shell_commands import parse_command, printed_commands

API = "https://api.test"
COMMAND = f"{API}/v1/knowledge/wiki/command"
PROJECT = "883b8d78-5edb-4dd9-a5ac-5e2f8c6fee3d"
PAGE_ID = "3d9356d4-3ae1-4aad-8da4-1ae328e8e6fa"
GENERIC = "api knowledge run-wiki-command"
READ_ARGUMENTS = "beakr/engines/tools/graph/wiki_read_arguments.py"
runner = CliRunner()

#: The door's arguments as the vendored API contract has them: the options the
#: generic command is built from.
FIELDS: dict[str, dict[str, Any]] = next(
    {field["name"]: field for field in entry["body"]["fields"]}
    for entry in operations()
    if entry["action"] == "run-wiki-command"
)
#: Arguments the door takes that the vendored contract does not have yet.
NEWER = ("after_page_id", "target", "output_mode", "include_sources")


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _engine_read_arguments() -> dict[str, frozenset[str]]:
    """The engine's own table of what each read command takes (never in the snapshot)."""
    for root in _candidates():
        if not is_snapshot(root) and (root / READ_ARGUMENTS).exists():
            spec = importlib.util.spec_from_file_location(
                "_engine_wiki_read_arguments", root / READ_ARGUMENTS
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return dict(module.READ_ARGUMENTS)
    message = f"needs a beakr-engine checkout with {READ_ARGUMENTS}"
    configured = os.environ.get("BEAKR_ENGINE_PATH")
    if configured and not is_snapshot(Path(configured)):
        _missing(message)
    pytest.skip(message)
    raise AssertionError("unreachable")


def _dedicated(command: str, argument: str) -> bool:
    """Whether the ``beakr kb`` command for ``command`` has a flag for ``argument``."""
    spec = COMMANDS[command]
    return (
        argument == spec.positional
        or argument in spec.options
        or any(key == argument for key, _ in spec.switches)
    )


def _samples(argument: str, *, dedicated: bool) -> list[Any]:
    """Values the door takes for ``argument``, and one the generic option does not."""
    field = FIELDS.get(argument, {"type": "string"})
    if field["type"] == "boolean":
        return [True, False]
    if field["type"] == "integer":
        return [7]
    if field["type"] == "object":
        return [{"from": "facts"}]
    values: list[Any] = list(field.get("enum") or [f"{argument} value"])
    # A value outside an option's choices (the door has gained one since the
    # contract was vendored) must not print a flag Click refuses.
    return values if dedicated else [*values, "newer choice"]


def _carried(command: str, argument: str, value: Any, printed: str) -> bool:
    """Whether ``printed`` parses and sends ``argument`` as ``value``."""
    path, given = parse_command(printed)
    if path == GENERIC:
        if "data" in given:
            return json.loads(given["data"]).get(argument) == value
        if value is False:
            # Click reads `--no-flag` as False, which the helper leaves out.
            return f"--no-{argument.replace('_', '-')}" in printed.split()
        return given.get(f"b__{argument}") == value
    spec = COMMANDS[command]
    assert path == spec.path, printed
    if value is False or spec.switches.get((argument, value)) == "":
        return True  # the command's default: nothing to pass
    if (argument, value) in spec.switches:
        return spec.switches[(argument, value)] in printed.split()
    return value in given.values()


# ---------------------------------------------------------------------------
# Every argument, every command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", sorted(DOOR_COMMANDS))
def test_every_door_argument_of_every_command_prints_a_command_that_parses(command: str) -> None:
    arguments = {*FIELDS, *NEWER} - {"command", "scope"}
    if command in COMMANDS:
        arguments |= {*COMMANDS[command].options, *GENERIC_ONLY.get(command, ())}

    for argument in sorted(arguments):
        dedicated = command in COMMANDS and _dedicated(command, argument)
        for value in _samples(argument, dedicated=dedicated):
            # With the project, as every continuation of a scoped read has it.
            printed = shell_command(command, {argument: value, "scope": PROJECT})
            assert _carried(command, argument, value, printed), printed
            assert f" {PROJECT}" in printed or f'"scope": "{PROJECT}"' in printed, printed


# Not `query`: the engine validates its input elsewhere (`wiki_card_query_input`).
@pytest.mark.parametrize("command", sorted(set(COMMANDS) - {"query"}))
def test_every_argument_the_engine_takes_has_a_flag_or_is_knowingly_generic(command: str) -> None:
    takes = _engine_read_arguments()[command]
    generic = GENERIC_ONLY.get(command, frozenset())

    for argument in sorted(takes):
        assert _dedicated(command, argument) != (argument in generic), (
            f"`{command}` takes `{argument}`: give `beakr {COMMANDS[command].path}` a flag for "
            "it in kb_shell_commands.COMMANDS, or list it in GENERIC_ONLY (not both)"
        )
    assert generic <= takes, f"GENERIC_ONLY[{command!r}] names arguments the door does not take"


def test_generic_only_names_commands_that_have_a_kb_command() -> None:
    assert set(GENERIC_ONLY) <= set(COMMANDS)


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        # No option of the generic command: the contract it is built from predates them.
        ("ls", {"sort_by": "title", "after_page_id": PAGE_ID}),
        ("grep", {"query": "NW-217", "target": "title"}),
        # A value outside the option's choices.
        ("grep", {"query": "NW-217", "mode": "newer mode"}),
        # A command the generic `--command` does not list.
        ("newer_command", {"page": "Assay"}),
    ],
)
def test_a_call_the_generic_command_has_no_option_for_goes_in_its_json_body(
    command: str, arguments: dict[str, Any]
) -> None:
    printed = shell_command(command, arguments)

    path, given = parse_command(printed)
    assert path == GENERIC
    assert json.loads(given["data"]) == {"command": command, **arguments}
    assert set(given) == {"data"}, printed


@respx.mock
def test_the_json_body_form_sends_the_whole_call() -> None:
    door = respx.post(COMMAND).mock(
        return_value=Response(200, json={"content": [], "outcome": {"status": "success"}})
    )
    arguments = {"sort_by": "title", "after_title": "Kickoff Notes", "after_page_id": PAGE_ID}
    printed = shell_command("ls", {**arguments, "scope": PROJECT})
    _, given = parse_command(printed)

    result = runner.invoke(app, ["api", "knowledge", "run-wiki-command", "--data", given["data"]])

    assert result.exit_code == 0, result.output
    assert json.loads(door.calls.last.request.content) == {
        "command": "ls",
        **arguments,
        "scope": PROJECT,
    }


# ---------------------------------------------------------------------------
# The live defect: a cut `kb ls` names a continuation that runs
# ---------------------------------------------------------------------------


def _cut_listing() -> dict[str, Any]:
    cursor = {"after_title": "Kickoff Notes", "after_page_id": PAGE_ID}
    resend = {"page_type": "topic", "limit": 1, **cursor, "changed_since": "2026-11-12"}
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    "89 matching page(s):\nFilters: changed since 2026-11-12 (UTC day).\n\n"
                    "  - [topic] Kickoff Notes  [Helios Team]  -- Kickoff Notes\n\n"
                    "  Showing 1 of 89. This listing is NOT complete: continue with "
                    f"ls {json.dumps(resend)}."
                ),
            }
        ],
        "outcome": {"status": "success"},
        "coverage": {"complete": False, "returned": 1, "total": 89, "continue_with": cursor},
        "next": [
            "Not everything is shown: call ls again with the same filters and "
            f'after_title="Kickoff Notes" and after_page_id="{PAGE_ID}" to read the rest.'
        ],
        "sources": [],
    }


@respx.mock
def test_a_cut_filtered_listing_names_a_continuation_that_runs() -> None:
    door = respx.post(COMMAND).mock(return_value=Response(200, json=_cut_listing()))
    asked = ["kb", "ls", "--changed-since", "2026-11-12", "--type", "topic", "--limit", "1"]
    asked += ["--after-title", "Hostile; echo PWNED", "--all"]

    listing = runner.invoke(app, asked)

    assert listing.exit_code == 0, listing.output
    commands = printed_commands(listing.stdout)
    continuations = [command for command in commands if "--after-page-id" in command]
    assert len(continuations) == 2, listing.stdout  # the listing's last line, and `next`
    for command in continuations:
        path, given = parse_command(command)
        assert path == "kb ls", command
        assert given["after_title"] == "Kickoff Notes"
        assert given["after_page_id"] == PAGE_ID
        # `--all` is not in the door call: the continuation must repeat it.
        assert given["all_scopes"] is True

    # Pasted, the continuation reaches the door with both halves of the cursor.
    pasted = runner.invoke(app, shlex.split(continuations[0])[1:])
    assert pasted.exit_code == 0, pasted.output
    sent = json.loads(door.calls.last.request.content)
    assert sent["after_title"] == "Kickoff Notes"
    assert sent["after_page_id"] == PAGE_ID
    assert sent["changed_since"] == "2026-11-12"


def test_ls_after_page_id_without_an_author_filter_is_refused_like_after_title() -> None:
    result = runner.invoke(app, ["kb", "ls", "--after-page-id", PAGE_ID])

    assert result.exit_code == 2, result.output
    assert "--after-page-id" in result.output


def test_a_reply_cursor_is_the_continuation_whatever_the_prose_names() -> None:
    # The prose names only the title; the reply's coverage holds the page id too.
    body = {
        "content": "2 matching page(s):\n\n  - [topic] Kickoff Notes",
        "coverage": {
            "complete": False,
            "continue_with": {"after_title": "Kickoff Notes", "after_page_id": PAGE_ID},
        },
        "next": [
            "Not everything is shown: call ls again with the same filters and "
            'after_title="Kickoff Notes" to read the rest.'
        ],
    }
    call = DoorCall("ls", {"changed_by": "me", "limit": 1, "scope": PROJECT})

    shown = shell_reply("knowledge_base", body, call)

    command = next(c for c in printed_commands(shown) if "--after-title" in c)
    assert parse_command(command) == (
        "kb ls",
        {
            "changed_by": "me",
            "limit": 1,
            "project": PROJECT,
            "after_title": "Kickoff Notes",
            "after_page_id": PAGE_ID,
        },
    )
