"""Auth commands: login, logout, whoami, dev, set-scope."""

from __future__ import annotations

import typer

from beakr_cli import config
from beakr_cli.client import api_get, get_client
from beakr_cli.output import console, err_console

app = typer.Typer(help="Authenticate with Beakr.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_project() -> None:
    """Fetch projects from the API and prompt the user to select one."""
    with get_client() as c:
        try:
            resp = c.get("/v1/projects")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            err_console.print(f"Failed to fetch projects: {exc}")
            raise typer.Exit(1)

    projects = data if isinstance(data, list) else data.get("projects", data)
    if not projects:
        err_console.print("No projects found in this organization.")
        raise typer.Exit(1)

    console.print()
    console.print("[bold]Available projects:[/bold]")
    for i, p in enumerate(projects, 1):
        name = p.get("name", "Untitled")
        desc = p.get("description", "") or ""
        suffix = f" -- {desc}" if desc else ""
        console.print(f"  {i}. {name}{suffix}")

    console.print()
    choice = typer.prompt("Select a project", type=int)

    if choice < 1 or choice > len(projects):
        err_console.print(f"Invalid choice. Enter a number between 1 and {len(projects)}.")
        raise typer.Exit(1)

    selected = projects[choice - 1]
    project_id = str(selected["id"])
    project_name = selected.get("name", "")

    config.set_key("project_id", project_id)
    console.print(f"Default project: [bold]{project_name}[/bold] ({project_id})")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


_ENV_URLS = {
    "prod": "https://api.thebeakr.com",
    "sandbox": "https://api-sandbox.thebeakr.com",
}


@app.command()
def login(
    api_key: str = typer.Option(
        ..., prompt="API key", hide_input=True, help="Your Beakr API key."
    ),
    env: str = typer.Option(
        "prod", "--env", "-e", help="Environment: prod or sandbox.",
    ),
    api_url: str = typer.Option(
        None, help="Custom API base URL (overrides --env)."
    ),
) -> None:
    """Save API credentials and select a default project."""
    resolved_url = api_url or _ENV_URLS.get(env)
    if not resolved_url:
        err_console.print(f"Unknown environment: {env}. Use 'prod' or 'sandbox'.")
        raise typer.Exit(1)

    config.set_key("api_key", api_key)
    config.set_key("api_url", resolved_url)

    # Verify credentials
    try:
        data = api_get("/v1/me")
        user = data.get("user", data)
        org = data.get("personal_org", data)
        console.print(
            f"Authenticated as [bold]{user.get('display_name', user.get('primary_email', 'unknown'))}[/bold] "
            f"in [bold]{org.get('name', org.get('slug', 'unknown'))}[/bold]"
        )
    except Exception as exc:
        err_console.print(f"Authentication failed: {exc}")
        config.delete_key("api_key")
        raise typer.Exit(1)

    if typer.confirm("Select a default project?", default=False):
        _pick_project()
    else:
        config.delete_key("project_id")
        console.print("No default scope set (org-wide).")


@app.command()
def logout() -> None:
    """Remove stored credentials and scope."""
    config.delete_key("api_key")
    config.delete_key("dev_identity_id")
    config.delete_key("dev_email")
    config.delete_key("dev_display_name")
    config.delete_key("org_id")
    config.delete_key("project_id")
    console.print("Logged out.")


@app.command()
def whoami() -> None:
    """Show current user and org."""
    try:
        data = api_get("/v1/me")
        user = data.get("user", data)
        org = data.get("personal_org", data)
        console.print(f"User:  {user.get('display_name', user.get('primary_email', 'unknown'))}")
        console.print(f"Org:   {org.get('name', org.get('slug', 'unknown'))}")
        active_org = config.get("org_id")
        if active_org:
            console.print(f"Org override: {active_org}")
        scope_project = config.get("project_id")
        if scope_project:
            console.print(f"Scope: project {scope_project}")
        else:
            console.print("Scope: [dim]none -- org-wide (run beakr auth set-scope)[/dim]")
    except Exception as exc:
        err_console.print(f"Failed: {exc}")
        raise typer.Exit(1)


@app.command()
def dev(
    identity: str = typer.Option(
        ..., "--identity", "-i", help="Dev identity ID for local API."
    ),
    email: str = typer.Option(
        ..., "--email", "-e", help="Dev email for local API."
    ),
    display_name: str = typer.Option(
        None, "--name", "-n", help="Display name."
    ),
    api_url: str = typer.Option(
        "http://localhost:8000", help="Local API URL."
    ),
) -> None:
    """Configure for local development and optionally select a default project."""
    config.set_key("dev_identity_id", identity)
    config.set_key("dev_email", email)
    config.set_key("api_url", api_url)
    if display_name:
        config.set_key("dev_display_name", display_name)
    # Clear prod API key so dev headers take priority
    config.delete_key("api_key")
    console.print(f"Dev mode: {identity} ({email}) -> {api_url}")

    if typer.confirm("Select a default project?", default=True):
        _pick_project()
    else:
        config.delete_key("project_id")
        console.print("No default scope set (org-wide).")


@app.command("set-scope")
def set_scope(
    project_id: str | None = typer.Option(None, "--project", "-p", help="Project ID (skip interactive picker)."),
) -> None:
    """Change the default project scope."""
    if project_id:
        config.set_key("project_id", project_id)
        console.print(f"Scope set to project {project_id}")
    else:
        _pick_project()


@app.command("set-org")
def set_org(
    org_id: str = typer.Argument(..., help="Organization UUID or slug for X-Org-Id."),
) -> None:
    """Set the active organization for CLI and MCP requests."""
    config.set_key("org_id", org_id)
    console.print(f"Org set to {org_id}")


@app.command("clear-org")
def clear_org() -> None:
    """Clear the active organization override."""
    config.delete_key("org_id")
    console.print("Org override cleared.")
