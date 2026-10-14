"""Resource commands use the spec's verbs (spec W7 item 2).

The spec fixes the grammar as ``beakr <resource> <verb>`` with the verbs ``ls``,
``show``, ``create``, ``edit`` and ``rm``. Root cause this guards: ``beakr kb
proposals`` shipped its listing as ``list`` -- the only one of 179 commands to do
so -- so ``beakr kb proposals ls`` failed with "No such command" for anyone who
had learned the verb from every other resource. The gate walks the Typer tree and
fails on any visible command named with a synonym of a spec verb.

What the spec does not constrain, and this gate therefore allows:

* Domain verbs beyond the five (``revoke``, ``archive``, ``restore``, ``accept``,
  ``dismiss``, ``invite``, ``role``, ``sync`` ...). The spec itself names them
  (W3.1-W3.16), so the gate forbids only words that *duplicate* a spec verb.
* ``set`` on settings singletons (``org policy set``, ``org retention set``): a
  singleton has no ``create`` or ``rm``, and ``set`` replaces its value whole.
* ``add`` on membership (``projects members add``): it attaches an existing person
  to a project rather than creating a resource.
* ``beakr api <area> <action>``: generated one-per-operation from the public
  contract (``beakr_cli.commands.api``) and named after the operation, not the
  resource grammar. It is built lazily and is not in the Typer tree walked here.

Renamed verbs stay registered as hidden, deprecated aliases so existing scripts
keep working; hidden commands are therefore outside the gate, and a separate test
checks that each hidden alias still runs the same code as its visible verb.
"""

from __future__ import annotations

import inspect

import click
import typer

from beakr_cli.main import app

#: Spec verb each synonym duplicates. A visible command named with one of these
#: words should use the spec verb instead.
SYNONYM_OF_SPEC_VERB = {
    "list": "ls",
    "get": "show",
    "view": "show",
    "describe": "show",
    "info": "show",
    "display": "show",
    "new": "create",
    "make": "create",
    "update": "edit",
    "modify": "edit",
    "change": "edit",
    "delete": "rm",
    "remove": "rm",
    "del": "rm",
    "destroy": "rm",
}

#: Visible commands allowed to use a synonym anyway, each with its reason.
GRAMMAR_EXEMPT_COMMANDS = {
    # Root-level verb on the CLI itself (self-upgrade of the installed package),
    # not `<resource> edit`; `pip`/`brew` users expect the word `update`.
    "update": "upgrades the installed CLI; not an edit of a resource",
}

#: Hidden aliases kept for compatibility, mapped to the visible spec verb that
#: replaced each one.
HIDDEN_ALIASES = {
    "kb proposals list": "kb proposals ls",
}


def _all_commands() -> dict[str, click.Command]:
    found: dict[str, click.Command] = {}

    def walk(command: click.Command, path: list[str]) -> None:
        if path:
            found[" ".join(path)] = command
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                walk(sub, [*path, name])

    walk(typer.main.get_command(app), [])
    return found


def _visible_leaves() -> dict[str, click.Command]:
    commands = _all_commands()
    return {
        path: command
        for path, command in commands.items()
        if not isinstance(command, click.Group)
        and not command.hidden
        # A command under a hidden group is hidden too.
        and not any(
            commands[" ".join(path.split()[:i])].hidden for i in range(1, len(path.split()))
        )
    }


def test_no_visible_command_uses_a_synonym_of_a_spec_verb():
    offenders = sorted(
        f"beakr {path} (use `{SYNONYM_OF_SPEC_VERB[path.split()[-1]]}`)"
        for path in _visible_leaves()
        if path.split()[-1] in SYNONYM_OF_SPEC_VERB and path not in GRAMMAR_EXEMPT_COMMANDS
    )
    assert offenders == [], f"commands outside the spec grammar: {offenders}"


def test_the_grammar_exemptions_name_real_visible_commands_that_need_them():
    # A stale entry would silently exempt a future command of the same name.
    leaves = _visible_leaves()
    for path in GRAMMAR_EXEMPT_COMMANDS:
        assert path in leaves, f"exempt command {path!r} no longer exists or is hidden"
        assert path.split()[-1] in SYNONYM_OF_SPEC_VERB, f"{path!r} needs no exemption"


def test_every_hidden_alias_runs_the_same_code_as_its_spec_verb():
    commands = _all_commands()
    for alias, canonical in HIDDEN_ALIASES.items():
        assert commands[alias].hidden, f"{alias!r} should be hidden"
        assert not commands[canonical].hidden, f"{canonical!r} should be visible"
        # Typer wraps each registration's callback; both wrap the one handler.
        assert inspect.unwrap(commands[alias].callback) is inspect.unwrap(
            commands[canonical].callback
        )


def test_hidden_listing_alias_is_not_shown_in_help():
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["kb", "proposals", "--help"])
    assert result.exit_code == 0, result.output
    commands = [line.split()[1] for line in result.stdout.splitlines() if line.startswith("│ ")]
    assert "ls" in commands
    assert "list" not in commands
