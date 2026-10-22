"""Entry point for the beakr CLI.

``internal_app`` is every command the CLI has; ``app``, the entry point, is the
published part of it (:mod:`beakr_cli.command_surface`): the commands over the
documented API plus sign-in, hooks, install, ``doctor``, the Codex wrapper and
the MCP server. The rest stay in ``internal_app`` for tests and later exposure.
"""

from __future__ import annotations

import typer
from rich.markup import escape

from beakr_cli.command_surface import core_tree
from beakr_cli.commands import (
    activity,
    auth,
    codex_wrapper,
    doctor,
    documents,
    end_user_tokens,
    external_users,
    files,
    hooks,
    integrations,
    interview_links,
    interviews,
    jobs,
    kb_cards,
    kb_entities,
    keys,
    labels,
    members,
    memory,
    memory_store,
    org,
    outputs,
    profile,
    project_members,
    projects,
    runs,
    skills,
    usage,
    workflows,
)
from beakr_cli.commands.kb import app as kb_app
from beakr_cli.commands.workspace import app as workspace_app
from beakr_cli.errors import BeakrGroup
from beakr_cli.project_ref import project_ref_callback

internal_app = typer.Typer(
    name="beakr",
    # Maps API errors to stable exit codes: 3 auth, 4 not found, 5 conflict.
    cls=BeakrGroup,
    help=(
        "CLI for Beakr: sign in, manage projects and members, upload files, read and "
        "write Knowledge, sync skills, and connect Claude Code and Codex. Every command "
        "takes --json; exit codes: 0 ok, 1 error, 2 usage, 3 auth, 4 not found, 5 conflict."
    ),
    no_args_is_help=True,
)

internal_app.add_typer(kb_app, name="kb")
internal_app.add_typer(workspace_app, name="workspace")
for _module in (
    auth,
    codex_wrapper,
    keys,
    projects,
    project_members,
    members,
    org,
    files,
    documents,
    kb_cards,
    kb_entities,
    profile,
    skills,
    memory,
    memory_store,
    hooks,
    activity,
    integrations,
    runs,
    workflows,
    outputs,
    interviews,
    interview_links,
    end_user_tokens,
    external_users,
    labels,
    jobs,
    usage,
    doctor,
):
    _module.register(internal_app)


#: Commands that must not print the stale-version notice: ``mcp`` owns stdio for
#: the protocol, and ``version``/``update`` report update status themselves.
_NO_UPDATE_NOTICE = frozenset({"mcp", "version", "update"})


def _print_version(value: bool) -> None:
    """``beakr --version``: the installed version, offline (``beakr version`` checks PyPI)."""
    if value:
        from beakr_cli import __version__

        print(f"beakr-cli {__version__}")
        raise typer.Exit()


@internal_app.callback()
def _main(
    ctx: typer.Context,
    show_version: bool = typer.Option(
        False,
        "--version",
        "-V",
        is_eager=True,
        callback=_print_version,
        help="Print the CLI version and exit (`beakr version` also checks for updates).",
    ),
) -> None:
    """Move any key an older CLI left in config.json; tell an interactive user they are behind."""
    import sys

    from beakr_cli import config
    from beakr_cli.output import err_console

    try:
        # CLI 0.3.x saved API keys in plain text in config.json (0644).
        config.migrate_secrets()
    except Exception as exc:  # never block the command; the key still works from there
        err_console.print(
            "[yellow]Could not move the saved API key to secure storage: "
            f"{escape(str(exc))}[/yellow]"
        )

    if ctx.invoked_subcommand in _NO_UPDATE_NOTICE or not sys.stderr.isatty():
        return
    from beakr_cli.updates import get_update_status

    status = get_update_status(timeout=1.5)
    if status.update_available:
        err_console.print(
            f"[yellow]beakr-cli {escape(str(status.latest))} is available "
            f"(you have {escape(str(status.current))}). Run [bold]beakr update[/bold].[/yellow]"
        )


_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


@internal_app.command()
def version(
    offline: bool = typer.Option(False, "--offline", help="Do not check PyPI."),
    json_out: bool = _json_opt,
) -> None:
    """Show the CLI version and whether a newer release is available."""
    from datetime import timedelta

    from beakr_cli import __version__
    from beakr_cli.output import print_json
    from beakr_cli.updates import (
        BOX_INSTRUCTIONS,
        CHECK_OFF,
        bundled_with_box,
        check_disabled,
        detect_install,
        get_update_status,
    )

    # The CLI a self-hosted installation bundles is upgraded with it, so PyPI's
    # latest release says nothing about it; with the check off PyPI is never
    # asked. Either way say so, not "could not be checked".
    on_box = not offline and bundled_with_box(detect_install())
    check_off = not offline and not on_box and check_disabled()
    if json_out:
        report: dict[str, object] = {"version": __version__}
        if on_box:
            report.update(bundled_with_box=True, instructions=BOX_INSTRUCTIONS)
        elif check_off:
            report.update(update_check="off")
        elif not offline:
            status = get_update_status(max_age=timedelta(hours=1))
            report.update(
                latest=status.latest,
                update_available=status.update_available,
                published_older=status.published_older,
                instructions=detect_install().instructions if status.update_available else None,
            )
        print_json(report)
        return

    # The first line stays machine-readable: `beakr update` parses it from the
    # upgraded binary to confirm what is now on PATH.
    print(f"beakr-cli {__version__}")
    if offline:
        return
    if on_box:
        print(f"Bundled with this Beakr installation. {BOX_INSTRUCTIONS}")
        return
    if check_off:
        print(CHECK_OFF)
        return
    status = get_update_status(max_age=timedelta(hours=1))
    if status.latest is None and status.published_older is not None:
        print(f"This build is newer than the latest release ({status.published_older})")
    elif status.latest is None:
        print("Latest release: unknown (PyPI could not be checked)")
    elif status.update_available:
        print(f"Update available: {status.latest}. {detect_install().instructions}")
    else:
        print(f"No newer release (latest release: {status.latest})")


@internal_app.command()
def update(
    check: bool = typer.Option(
        False, "--check", help="Only report whether an update is available."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Run the upgrade even if already on the latest release."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Upgrade beakr-cli to the latest release and refresh installed skills."""
    from beakr_cli.output import human_output_to_stderr, print_json

    with human_output_to_stderr(json_out):
        result = _update(check=check, force=force)
    if json_out:
        print_json(result)


def _update(*, check: bool, force: bool) -> dict[str, object]:
    """``beakr update``; human progress goes to ``console``, the outcome is returned."""
    from datetime import timedelta

    from beakr_cli import __version__
    from beakr_cli.output import console, err_console
    from beakr_cli.updates import (
        BOX_INSTRUCTIONS,
        CHECK_OFF,
        bundled_with_box,
        check_disabled,
        check_off_fix,
        detect_install,
        get_update_status,
        installed_version_on_path,
        run_upgrade,
    )

    if bundled_with_box(detect_install()):
        console.print(f"Bundled with this Beakr installation. {BOX_INSTRUCTIONS}", markup=False)
        if not check:
            raise typer.Exit(1)
        return {"current": __version__, "bundled_with_box": True, "instructions": BOX_INSTRUCTIONS}
    status = get_update_status(max_age=timedelta(0))
    report: dict[str, object] = {
        "current": status.current,
        "latest": status.latest,
        "update_available": status.update_available,
        "updated": False,
    }
    info = detect_install()
    if status.latest is None and check_disabled():
        # The check never ran: PyPI was not asked, so it was not "unreachable".
        err_console.print(f"{CHECK_OFF} {check_off_fix()}", markup=False)
        if check or not force:
            raise typer.Exit(1)
    elif status.latest is None:
        err_console.print(
            "[yellow]Could not determine the latest release: PyPI was unreachable or is "
            "still publishing a new release. Try again in a few minutes.[/yellow]"
        )
        if check or not force:
            raise typer.Exit(1)
    elif not status.update_available and not force:
        console.print(
            f"No update needed: installed {status.current}, latest release {status.latest}.",
            markup=False,
        )
        return report
    else:
        console.print(f"Installed: {status.current}   Latest: {status.latest}", markup=False)

    if check:
        console.print(info.instructions, markup=False)
        return {**report, "instructions": info.instructions}

    if info.upgrade_command is None:
        err_console.print(
            f"Cannot upgrade automatically ({info.method.value}). {info.instructions}", markup=False
        )
        raise typer.Exit(1)

    console.print(f"[dim]$ {escape(' '.join(info.upgrade_command))}[/dim]")
    result = run_upgrade(info, expected_version=status.latest)
    if result.output:
        console.print(f"[dim]{escape(str(result.output))}[/dim]", highlight=False)
    if not result.ok:
        err_console.print(f"[red]{escape(str(result.message))}[/red]")
        raise typer.Exit(1)
    now = installed_version_on_path() or "unknown"
    console.print(
        f"[green]{escape(str(result.message))}[/green] beakr on PATH is now {escape(str(now))}."
    )
    console.print("Restart Claude Code / Codex so they launch the new MCP server.")
    return {**report, "updated": True, "on_path": now}


@internal_app.command()
def research(
    query: str = typer.Argument(..., help="The question to answer."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Project id, slug or name to scope the query.",
        callback=project_ref_callback,
    ),
    quick: bool = typer.Option(
        False,
        "--quick",
        help="A short cited answer within 15 seconds from one search (no connected services).",
    ),
) -> None:
    """Ask a question and get a researched answer with citations."""
    from beakr_cli.cited_answer import print_research_result, research_lines
    from beakr_cli.output import err_console, machine_output
    from beakr_cli.scope import scope_params
    from beakr_cli.sdk import get_sdk

    project_id = scope_params(project=project).get("project_id")

    err_console.print("[dim]Researching...[/dim]")
    try:
        with get_sdk() as sdk:
            data = sdk.knowledge.research(query, project_id=project_id, quick=quick)
    except Exception as e:
        err_console.print(f"[red]Error: {escape(str(e))}[/red]")
        raise typer.Exit(1)

    if machine_output(data, json_out=json_out, lines=research_lines):
        return

    print_research_result(data)


@internal_app.command()
def mcp() -> None:
    """Start the MCP server for Claude Code / AI assistant integration.

    Speaks MCP over stdio; there is no --json.
    """
    # No --json: stdout carries the MCP protocol itself (see JSON_EXEMPT_COMMANDS
    # in tests/test_cli_json_everywhere.py).
    from beakr_cli.mcp_server import run_server

    run_server()


def _parse_install_args(client: str, scope: str):
    from beakr_cli.commands.install import Client, Scope

    try:
        client_enum = Client(client)
    except ValueError:
        typer.echo(f"Invalid --client '{client}'. Choose: auto, claude, codex, all.")
        raise typer.Exit(2)
    try:
        scope_enum = Scope(scope)
    except ValueError:
        typer.echo(f"Invalid --scope '{scope}'. Choose: user, project.")
        raise typer.Exit(2)
    return client_enum, scope_enum


@internal_app.command()
def install(
    client: str = typer.Option(
        "auto",
        "--client",
        "-c",
        help="Which client: auto (detect installed), claude, codex, or all.",
    ),
    scope: str = typer.Option(
        "user",
        "--scope",
        "-s",
        help="user (~/.claude, ~/.codex) or project (./.claude, ./.agents).",
    ),
    uninstall: bool = typer.Option(
        False,
        "--uninstall",
        help="Remove installed skills and commands.",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files."),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Overwrite skills and commands only where they are already installed.",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Install Beakr skills and slash commands into Claude Code and/or Codex."""
    from beakr_cli.commands.install import install_command, refresh_installed_assets
    from beakr_cli.output import human_output_to_stderr, print_json

    with human_output_to_stderr(json_out):
        if refresh:
            result: dict[str, object] = {
                "action": "refresh",
                "refreshed": refresh_installed_assets(),
            }
        else:
            client_enum, scope_enum = _parse_install_args(client, scope)
            result = install_command(
                client=client_enum, scope=scope_enum, uninstall=uninstall, force=force
            )
    if json_out:
        print_json(result)


@internal_app.command()
def setup(
    client: str = typer.Option(
        "auto",
        "--client",
        "-c",
        help="Which client: auto (detect installed), claude, codex, or all.",
    ),
    scope: str = typer.Option(
        "user",
        "--scope",
        "-s",
        help="user (~/.claude, ~/.codex) or project (./.claude, ./.agents).",
    ),
    no_auth: bool = typer.Option(False, "--no-auth", help="Skip the API key prompt."),
    no_skills: bool = typer.Option(False, "--no-skills", help="Skip skills install."),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="Skip MCP server registration."),
    uninstall: bool = typer.Option(
        False,
        "--uninstall",
        help="Remove skills and MCP registration.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing files and registrations.",
    ),
    hooks: bool | None = typer.Option(
        None,
        "--hooks/--no-hooks",
        help=(
            "Install session hooks (context at start, session capture as it runs). "
            "Default: ask when interactive, skip when not."
        ),
    ),
    json_out: bool = _json_opt,
) -> None:
    """One-shot install: auth + skills + MCP + hooks for Claude Code and/or Codex."""
    from beakr_cli.commands.install import setup_command
    from beakr_cli.output import human_output_to_stderr, print_json

    client_enum, scope_enum = _parse_install_args(client, scope)
    with human_output_to_stderr(json_out):
        result = setup_command(
            client=client_enum,
            scope=scope_enum,
            skip_auth=no_auth,
            skip_skills=no_skills,
            skip_mcp=no_mcp,
            uninstall=uninstall,
            force=force,
            hooks=hooks,
        )
    if json_out:
        print_json(result)


#: The published command tree (the ``beakr`` entry point).
app = core_tree(internal_app)


if __name__ == "__main__":
    app()
