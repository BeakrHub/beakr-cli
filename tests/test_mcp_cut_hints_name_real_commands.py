"""Every truncation hint that names a ``beakr`` command names one that exists.

Root cause guarded (sandbox e2e, 2026-10-27): the workboard's cut hint told the
agent to run ``beakr api knowledge get-wiki-work-board``. The work board is not
in the public contract, so ``beakr api`` has no such command and an agent that
followed the hint hit "No such command". A hint is the agent's only next step
once a result is cut, so each command it names is resolved here against the
real command tree.
"""

from __future__ import annotations

import re
import shlex

import click
import pytest
import typer

from beakr_cli.main import app
from beakr_cli.mcp_result import CUT_HINTS

_COMMAND = re.compile(r"`(beakr [^`]+)`")


def _named_commands() -> list[tuple[str, str]]:
    return [
        (tool, match.group(1))
        for tool, hint in CUT_HINTS.items()
        for match in _COMMAND.finditer(hint)
    ]


def _resolves(command_line: str) -> bool:
    words = [w for w in shlex.split(command_line)[1:] if not w.startswith(("-", "<"))]
    group: click.Command = typer.main.get_command(app)
    ctx = click.Context(group)
    for word in words:
        if not isinstance(group, click.Group):
            return True  # the rest are the command's arguments
        sub = group.get_command(ctx, word)
        if sub is None:
            return False
        group = sub
    return not isinstance(group, click.Group)


@pytest.mark.parametrize(("tool", "command_line"), _named_commands())
def test_cut_hint_command_exists(tool: str, command_line: str) -> None:
    assert _resolves(command_line), f"{tool}'s cut hint names a missing command: {command_line}"


def test_workboard_hint_names_no_missing_api_command() -> None:
    assert "get-wiki-work-board" not in CUT_HINTS["workboard"]
    assert not _resolves("beakr api knowledge get-wiki-work-board")
