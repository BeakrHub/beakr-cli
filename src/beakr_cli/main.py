"""Entry point for the beakr CLI."""

from __future__ import annotations

import typer

from beakr_cli.commands.auth import app as auth_app
from beakr_cli.commands.kb import app as kb_app
from beakr_cli.commands.workspace import app as workspace_app

app = typer.Typer(
    name="beakr",
    help=(
        "CLI for Beakr's knowledge base. Query, search, and browse your team's "
        "knowledge base from the terminal."
    ),
    no_args_is_help=True,
)

app.add_typer(auth_app, name="auth")
app.add_typer(kb_app, name="kb")
app.add_typer(workspace_app, name="workspace")


#: Commands that must not print the stale-version notice: ``mcp`` owns stdio for
#: the protocol, and ``version``/``update`` report update status themselves.
_NO_UPDATE_NOTICE = frozenset({"mcp", "version", "update"})


@app.callback()
def _main(ctx: typer.Context) -> None:
    """Tell an interactive user, at most once a day of checking, that they are behind."""
    import sys

    if ctx.invoked_subcommand in _NO_UPDATE_NOTICE or not sys.stderr.isatty():
        return
    from beakr_cli.output import err_console
    from beakr_cli.updates import get_update_status

    status = get_update_status(timeout=1.5)
    if status.update_available:
        err_console.print(
            f"[yellow]beakr-cli {status.latest} is available "
            f"(you have {status.current}). Run [bold]beakr update[/bold].[/yellow]"
        )


@app.command()
def version(
    offline: bool = typer.Option(False, "--offline", help="Do not check PyPI."),
) -> None:
    """Show the CLI version and whether a newer release is available."""
    from datetime import timedelta

    from beakr_cli import __version__
    from beakr_cli.updates import detect_install, get_update_status

    # The first line stays machine-readable: `beakr update` parses it from the
    # upgraded binary to confirm what is now on PATH.
    print(f"beakr-cli {__version__}")
    if offline:
        return
    status = get_update_status(max_age=timedelta(hours=1))
    if status.latest is None:
        print("Latest release: unknown (could not reach PyPI)")
    elif status.update_available:
        print(f"Update available: {status.latest}. {detect_install().instructions}")
    else:
        print(f"No newer release (latest release: {status.latest})")


@app.command()
def update(
    check: bool = typer.Option(
        False, "--check", help="Only report whether an update is available."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Run the upgrade even if already on the latest release."
    ),
) -> None:
    """Upgrade beakr-cli to the latest release and refresh installed skills."""
    from datetime import timedelta

    from beakr_cli.output import console, err_console
    from beakr_cli.updates import (
        detect_install,
        get_update_status,
        installed_version_on_path,
        run_upgrade,
    )

    status = get_update_status(max_age=timedelta(0))
    info = detect_install()
    if status.latest is None:
        err_console.print("[yellow]Could not reach PyPI to check the latest release.[/yellow]")
        if check or not force:
            raise typer.Exit(1)
    elif not status.update_available and not force:
        console.print(
            f"No update needed: installed {status.current}, latest release {status.latest}."
        )
        return
    else:
        console.print(f"Installed: {status.current}   Latest: {status.latest}")

    if check:
        console.print(info.instructions)
        return

    if info.upgrade_command is None:
        err_console.print(
            f"Cannot upgrade automatically ({info.method.value}). {info.instructions}"
        )
        raise typer.Exit(1)

    console.print(f"[dim]$ {' '.join(info.upgrade_command)}[/dim]")
    result = run_upgrade(info)
    if result.output:
        console.print(f"[dim]{result.output}[/dim]", highlight=False)
    if not result.ok:
        err_console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    now = installed_version_on_path() or "unknown"
    console.print(f"[green]{result.message}[/green] beakr on PATH is now {now}.")
    console.print("Restart Claude Code / Codex so they launch the new MCP server.")


@app.command()
def research(
    query: str = typer.Argument(..., help="The question to answer."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        help="Project ID to scope the query.",
    ),
) -> None:
    """Ask a question and get a researched answer with citations."""
    from beakr_cli.client import api_post, scope_params
    from beakr_cli.output import console, err_console, is_piped, print_json, print_markdown

    params = scope_params(project=project)
    body = {"query": query, **params}

    err_console.print("[dim]Researching...[/dim]")
    try:
        data = api_post("/v1/knowledge/research", json=body)
    except Exception as e:
        err_console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if json_out or is_piped():
        print_json(data)
        return

    import re

    answer = data.get("answer", "")
    sources = data.get("sources", {})

    # Replace {{key}} tokens with [N] numbered refs
    seen_keys: list[str] = []
    def _replace_token(m):
        key = m.group(1)
        if key not in seen_keys:
            seen_keys.append(key)
        return f"[{seen_keys.index(key) + 1}]"

    formatted = re.sub(r"\{\{([^}]+)\}\}", _replace_token, answer)
    if formatted:
        print_markdown(formatted)

    if seen_keys:
        console.print("\n[bold]Sources:[/bold]")
        for i, key in enumerate(seen_keys, 1):
            src = sources.get(key, {})
            source_type = src.get("type", key.split(":")[0] if ":" in key else "unknown")
            title = src.get("title", "")
            url = src.get("web_view_url", "")
            line = f"  [{i}] ({source_type}) {title}"
            if url:
                line += f"\n      {url}"
            console.print(line)

    confidence = data.get("confidence", "")
    gaps = data.get("gaps", "")
    meta_parts = []
    if confidence:
        meta_parts.append(f"Confidence: {confidence}")
    if gaps:
        meta_parts.append(f"Gaps: {gaps}")
    if meta_parts:
        console.print(f"\n[dim]{' | '.join(meta_parts)}[/dim]")


@app.command()
def mcp() -> None:
    """Start the MCP server for Claude Code / AI assistant integration."""
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


@app.command()
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
) -> None:
    """Install Beakr skills and slash commands into Claude Code and/or Codex."""
    from beakr_cli.commands.install import install_command, refresh_installed_assets

    if refresh:
        refresh_installed_assets()
        return
    client_enum, scope_enum = _parse_install_args(client, scope)
    install_command(client=client_enum, scope=scope_enum, uninstall=uninstall, force=force)


@app.command()
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
) -> None:
    """One-shot install: auth + skills + MCP registration for Claude Code and/or Codex."""
    from beakr_cli.commands.install import setup_command

    client_enum, scope_enum = _parse_install_args(client, scope)
    setup_command(
        client=client_enum,
        scope=scope_enum,
        skip_auth=no_auth,
        skip_skills=no_skills,
        skip_mcp=no_mcp,
        uninstall=uninstall,
        force=force,
    )


if __name__ == "__main__":
    app()
