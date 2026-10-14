"""Which commands the ``beakr`` entry point registers: the documented core.

The published command tree is the commands over the documented public contract
plus everything sign-in, hooks, install, ``doctor``, the Codex wrapper and the
MCP server need. Every other command module stays, unchanged and tested: it is
attached to :data:`beakr_cli.main.internal_app` (the full tree, for tests and
for re-exposing later) but not to :data:`beakr_cli.main.app`, the entry point.
They are not registered at all rather than hidden, so ``--help``, completion
and the parity matrix see only the core.

To expose a command again, add its path here; ``tests/test_cli_core_surface.py``
checks every path names a real command.
"""

from __future__ import annotations

import copy

import typer
from typer.main import get_command_name, solve_typer_info_defaults

#: Space-separated command paths of the published tree. A group is kept when
#: any path under it is.
CORE_COMMANDS: frozenset[str] = frozenset(
    {
        # The CLI itself
        "version",
        "update",
        "doctor",
        "install",
        "setup",
        "mcp",
        "codex",
        # Account and access
        "whoami",
        "auth login",
        "auth logout",
        "auth status",
        "auth whoami",
        "auth token",
        "auth set-org",
        "auth clear-org",
        "auth set-scope",
        "auth dev",
        "keys ls",
        "keys create",
        "keys revoke",
        "keys scopes",
        # Embedded mode: tokens your backend mints for its users
        "end-user-tokens mint",
        "end-user-tokens ls",
        "end-user-tokens revoke",
        "external-users ls",
        "external-users create",
        "external-users show",
        "external-users edit",
        "external-users rm",
        "labels ls",
        "labels create",
        "labels show",
        "labels edit",
        "labels rm",
        "labels members ls",
        "labels members add",
        "labels members rm",
        "labels projects ls",
        "labels projects add",
        "labels projects rm",
        # Organization and people
        "org show",
        "org service-accounts ls",
        "org service-accounts create",
        "org service-accounts disable",
        "org service-accounts key",
        "members ls",
        "members role",
        "members rm",
        # Projects
        "init",
        "workspace projects",
        "projects ls",
        "projects create",
        "projects show",
        "projects archive",
        "projects rm",
        "projects members ls",
        "projects members add",
        "projects members rm",
        "projects members invite",
        # Files
        "upload",
        "files upload",
        "files ls",
        "files show",
        "files download",
        "files rm",
        # Background jobs (indexing, imports, syncs)
        "jobs show",
        "jobs wait",
        # Knowledge
        "kb ls",
        "kb cat",
        "kb sections",
        "kb grep",
        "kb search",
        "kb sources",
        "kb log",
        "kb entities",
        "kb rows",
        "kb add-fact",
        "kb add-connection",
        "kb add-finding",
        "kb edit-rows",
        "kb research",
        "kb proposals ls",
        "kb proposals list",  # hidden alias of `ls`
        "kb proposals show",
        "kb proposals accept",
        "kb proposals dismiss",
        "kb propose create",
        "kb propose edit",
        "kb propose replace",
        "kb propose move",
        "kb propose archive",
        # Ask
        "research",
        "ask",
        "runs ls",
        "runs show",
        "runs cancel",
        # Interviews: send a link, read the result
        "interviews link create",
        "interviews link ls",
        "interviews link show",
        "interviews link send",
        "interviews link revoke",
        # Conversations, and the agent session hooks built on them
        "context",
        "compound",
        "conversations ls",
        "conversations show",
        "conversations search",
        "conversations import",
        "conversations rm",
        "hooks install",
        "hooks uninstall",
        "hooks status",
        "hooks capture",
        "hooks learning",
        # Integrations
        "integrations ls",
        "integrations connect",
        "integrations status",
        "integrations disconnect",
        "integrations rm",
        "integrations tools",
        "integrations call",
    }
)


def _command_name(info: typer.models.CommandInfo) -> str:
    return info.name or get_command_name(info.callback.__name__)  # type: ignore[union-attr]


def _kept(path: str, keep: frozenset[str]) -> bool:
    return path in keep or any(entry.startswith(path + " ") for entry in keep)


def core_tree(
    full: typer.Typer, keep: frozenset[str] = CORE_COMMANDS, prefix: str = ""
) -> typer.Typer:
    """A copy of ``full`` holding only the commands in ``keep``.

    ``full`` and its sub-apps are left as they are (the copies get new command
    lists), so the full tree stays usable beside the published one.
    """
    tree = copy.copy(full)
    tree.registered_commands = [
        info for info in full.registered_commands if f"{prefix}{_command_name(info)}" in keep
    ]
    tree.registered_groups = []
    for group in full.registered_groups:
        name = solve_typer_info_defaults(group).name
        path = f"{prefix}{name}" if name else prefix.rstrip()
        if not _kept(path, keep):
            continue
        kept_group = copy.copy(group)
        kept_group.typer_instance = core_tree(
            group.typer_instance,  # type: ignore[arg-type]
            keep,
            f"{path} " if path else "",
        )
        tree.registered_groups.append(kept_group)
    return tree
