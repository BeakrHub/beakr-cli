"""The only place the CLI asks a person anything.

The CLI contract (spec W7 item 2) is "no prompts when stdin is not a TTY": a
script or agent has nobody to answer, so a prompt there blocks forever, or
reads whatever happens to be piped in as the answer. Every confirmation and
question therefore goes through these helpers, which refuse with a usage error
(exit 2) naming the flag that answers it instead. A direct ``typer.confirm`` or
``typer.prompt`` anywhere else fails ``tests/test_cli_prompt_guard.py``.
"""

from __future__ import annotations

from typing import NoReturn

import typer

from beakr_cli import output as _output
from beakr_cli.errors import EXIT_ERROR, EXIT_USAGE

# Questions are written to stderr (``err=True``) so a command's stdout carries
# only its result, even when a person answers a prompt along the way.


def _refuse(message: str) -> NoReturn:
    _output.err_console.print(message)
    raise typer.Exit(EXIT_USAGE)


def confirmed(prompt: str, yes: bool) -> bool:
    """True when ``--yes`` was given or the person confirms on a TTY.

    A "no" returns False, for a step the command can skip (accepting a
    proposal it has just created). No TTY and no ``--yes`` exits 2, the usage
    error a script can branch on.
    """
    if yes:
        return True
    if not _output.is_interactive():
        _refuse(f"Refusing without --yes when not running interactively ({prompt})")
    return typer.confirm(prompt, default=False, err=True)


def confirm_or_require_yes(prompt: str, yes: bool) -> None:
    """Confirm a consequential action on a TTY; without one, require ``--yes``.

    Declining exits 1 (the action did not happen); no TTY and no ``--yes``
    exits 2.
    """
    if not confirmed(prompt, yes):
        raise typer.Exit(EXIT_ERROR)


def confirm_if_interactive(prompt: str, *, default: bool) -> bool:
    """Ask an optional yes/no question; without a TTY the answer is "no".

    For extras a command can do without (pick a default project, install
    hooks): skipping them is always safe, so a script is never blocked.
    """
    if not _output.is_interactive():
        return False
    return typer.confirm(prompt, default=default, err=True)


def require_interactive(flag_hint: str) -> None:
    """Refuse up front, naming ``flag_hint``, when a question will need a TTY.

    For commands that must fetch something (a project list) before they can
    ask: refusing first spares a script the round trip.
    """
    if not _output.is_interactive():
        _refuse(f"Pass {flag_hint} when not running interactively.")


def prompt_choice(prompt: str, count: int, *, flag_hint: str) -> int:
    """Ask for a number from 1 to ``count``; return the 0-based index.

    Without a TTY, refuses and names ``flag_hint``, the flag that answers the
    question non-interactively.
    """
    require_interactive(flag_hint)
    choice = typer.prompt(prompt, type=int, err=True)
    if not 1 <= choice <= count:
        _output.err_console.print(f"Enter a number between 1 and {count}.")
        raise typer.Exit(EXIT_USAGE)
    return choice - 1


def prompt_secret(prompt: str, *, flag_hint: str) -> str:
    """Ask for a hidden value on a TTY; without one, refuse and name ``flag_hint``."""
    require_interactive(flag_hint)
    return typer.prompt(prompt, hide_input=True, err=True).strip()
