"""Parse a printed ``beakr ...`` command against the real command tree.

A hint is only as good as the command it names: these helpers take a command a
hint printed, fill the ``<slots>`` it leaves for the reader, and parse it with
Click exactly as the shell would, so a flag the CLI does not have fails the test.
"""

from __future__ import annotations

import re
import shlex
from functools import lru_cache
from typing import Any

import click
import typer

from beakr_cli.main import internal_app

_TICKED = re.compile(r"`(beakr [^`]+)`")


def printed_commands(text: str) -> list[str]:
    """Every backticked ``beakr ...`` command in ``text``."""
    return _TICKED.findall(text)


@lru_cache(maxsize=1)
def _root() -> click.Command:
    # Built once: a test that parses every argument of every command would
    # otherwise rebuild the whole tree thousands of times.
    return typer.main.get_command(internal_app)


def parse_command(command: str) -> tuple[str, dict[str, Any]]:
    """The command path and the parameters Click reads from ``command``; raises on a bad one."""
    filled = re.sub(r"<[^<>]+>", "1", command.replace("[", "").replace("]", ""))
    words = shlex.split(filled)
    assert words[0] == "beakr", command
    node: click.Command = _root()
    context = click.Context(node, info_name="beakr")
    arguments = words[1:]
    path = []
    while isinstance(node, click.Group):
        name, node, arguments = node.resolve_command(context, arguments)
        assert node is not None, command
        path.append(name)
        context = click.Context(node, info_name=name, parent=context)
    node.parse_args(context, list(arguments))
    assert not context.args, f"{command}: unparsed {context.args}"
    given = {key: value for key, value in context.params.items() if value not in (None, (), False)}
    return " ".join(path), given
