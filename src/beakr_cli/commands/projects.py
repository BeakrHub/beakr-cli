"""Project commands (``ls``, ``create``, ``show``, ``archive``, ``rm``) and ``beakr init``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.markup import escape

from beakr_cli.commands import project_knowledge, project_profile
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_binding import CAPTURE_FIELD, LEARNING_FIELD, read_binding, write_binding
from beakr_cli.project_ref import find_project, project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes, prompt_choice, require_interactive
from beakr_cli.sdk import get_sdk

app = typer.Typer(
    help="List, create, archive and delete projects; check and retry their Knowledge."
)

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


def _render_projects(rows: list[dict[str, Any]]) -> None:
    if not rows:
        err_console.print("No projects found.")
        return
    print_table(
        ["Name", "ID", "Type", "Status", "Description"],
        [
            [
                short(p.get("name"), 32),
                str(p.get("id", "")),
                str(p.get("project_type") or ""),
                str(p.get("status") or ""),
                short(p.get("description"), 40),
            ]
            for p in rows
        ],
        title="Projects",
    )


@app.command("ls")
def list_projects(
    member_only: bool = typer.Option(False, "--member-only", help="Only projects you belong to."),
    json_out: bool = _json_opt,
) -> None:
    """List projects you can see, including your personal project."""
    with get_sdk() as sdk:
        projects = sdk.projects.list(member_only=member_only or None)
    emit(projects, json_out=json_out, render=_render_projects)


@app.command("create")
def create_project(
    name: str = typer.Argument(..., help="Project name."),
    description: str | None = typer.Option(None, "--description", "-d"),
    tag: list[str] = typer.Option(None, "--tag", help="Tag (repeatable)."),
    json_out: bool = _json_opt,
) -> None:
    """Create a project."""
    with get_sdk() as sdk:
        project = sdk.projects.create(name, description=description, tags=list(tag or []) or None)
    emit(
        project,
        json_out=json_out,
        render=lambda p: console.print(
            f"Created project [bold]{escape(str(p.get('name')))}[/bold] ({escape(str(p['id']))})"
        ),
        hint=lambda p: f"beakr upload PATH -P {p.get('slug') or p.get('id')}",
    )


def _resolve_project_ref(sdk: Any, ref: str) -> dict[str, Any]:
    project = find_project(sdk.projects.list(), ref)
    if project is None:
        err_console.print(f"Project not found: {ref}", markup=False)
        raise typer.Exit(4)
    return project


@app.command("show")
def show_project(
    project: str = typer.Argument(..., help="Project id, slug or name."),
    json_out: bool = _json_opt,
) -> None:
    """Show a project."""
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        data = sdk.projects.get(str(found["id"]))

    def render(d: dict[str, Any]) -> None:
        console.print(f"[bold]{escape(str(d.get('name')))}[/bold] ({escape(str(d.get('id')))})")
        for label, key in (
            ("Type", "project_type"),
            ("Status", "status"),
            ("Owner", "owner_name"),
            ("Members", "member_count"),
            ("Updated", "updated_at"),
        ):
            if d.get(key) is not None:
                console.print(f"{label + ':':<9}{d.get(key)}", markup=False)
        if d.get("description"):
            console.print(f"\n{d['description']}", markup=False)

    emit(data, json_out=json_out, render=render)


@app.command("archive")
def archive_project(
    project: str = typer.Argument(..., help="Project id, slug or name."),
    json_out: bool = _json_opt,
) -> None:
    """Archive a project (reversible from the web app)."""
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        archived = sdk.projects.archive(str(found["id"]))
    emit(
        archived,
        json_out=json_out,
        render=lambda p: console.print(
            f"Archived project {p.get('name')} ({p.get('id')}).", markup=False
        ),
    )


@app.command("rm")
def delete_project(
    project: str = typer.Argument(..., help="Project id, slug or name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    json_out: bool = _json_opt,
) -> None:
    """Delete a project permanently (needs admin access to the project).

    There is no trash: a deleted project cannot be restored. Your personal project
    cannot be deleted. `beakr projects archive` hides a project reversibly.
    """
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        confirm_or_require_yes(
            f"Permanently delete project {found.get('name')}? This cannot be undone.",
            yes=yes,
        )
        sdk.projects.delete(str(found["id"]))
    emit(
        {"id": str(found.get("id")), "name": found.get("name"), "deleted": True},
        json_out=json_out,
        render=lambda d: err_console.print(
            f"Deleted {d['name']} ({d['id']}) permanently.", markup=False
        ),
        json_when_piped=False,
    )


def _pick_interactively(projects: list[dict[str, Any]]) -> dict[str, Any]:
    for index, project in enumerate(projects, 1):
        kind = project.get("project_type") or "standard"
        # stderr, like the question itself: stdout carries only the result.
        err_console.print(f"  {index}. {project.get('name')} ({kind})", markup=False)
    return projects[
        prompt_choice("Select a project", len(projects), flag_hint="--project or --create")
    ]


def _active_org_id(sdk: Any) -> str | None:
    """Best effort: a binding without an org still works, it is just not org-checked."""
    from beakr_sdk.errors import BeakrError

    try:
        return (sdk.me.active_org() or {}).get("id")
    except BeakrError:
        return None


def init_command(
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Project id, slug or name to bind.",
        callback=project_ref_callback,
    ),
    project_p: str | None = typer.Option(None, "-p", hidden=True, callback=project_ref_callback),
    create: str | None = typer.Option(
        None, "--create", help="Create a new project with this name and bind it."
    ),
    capture: bool | None = typer.Option(
        None,
        "--capture/--no-capture",
        help=(
            "Let the installed session hooks capture agent sessions run in this "
            "repository (off unless given; kept when rebinding the same project)."
        ),
    ),
    learning: bool | None = typer.Option(
        None,
        "--learning/--no-learning",
        help=(
            "Let captured sessions a person typed in teach your personal learning "
            "(off unless given; kept when rebinding the same project). Headless "
            "runs are never learned from."
        ),
    ),
    json_out: bool = _json_opt,
) -> None:
    """Bind the current directory to a Beakr project (.beakr/project.json)."""
    # -p was this option's short flag before -P everywhere; still accepted.
    project = project or project_p

    if project and create:
        err_console.print("Pass either --project or --create, not both.")
        raise typer.Exit(2)
    if not project and not create:
        # Refuse before fetching the project list there is nobody to choose from.
        require_interactive("--project or --create")

    with get_sdk() as sdk:
        if create:
            chosen = sdk.projects.create(create)
        else:
            projects = sdk.projects.list()
            if project:
                chosen = find_project(projects, project)
                if chosen is None:
                    err_console.print(f"Project not found: {project}", markup=False)
                    raise typer.Exit(4)
            else:
                if not projects:
                    err_console.print("No projects yet. Use --create NAME.")
                    raise typer.Exit(1)
                chosen = _pick_interactively(projects)
        base_url = sdk.base_url
        # Recorded so the binding is ignored under a credential for another
        # organization (project_binding.binding_applies).
        org_id = chosen.get("org_id") or _active_org_id(sdk)

    previous = read_binding(Path.cwd())
    binding: dict[str, Any] = {
        "project_id": str(chosen["id"]),
        "name": chosen.get("name"),
        "base_url": base_url,
        "org_id": str(org_id) if org_id else None,
    }
    # Capture consent and the learning opt-in are per project: each survives
    # re-running init for the same project, and is reset when the directory is
    # bound to another one.
    same_project = bool(previous and previous.get("project_id") == binding["project_id"])
    if capture is not None:
        binding[CAPTURE_FIELD] = capture
    elif same_project and previous and previous.get(CAPTURE_FIELD) is True:
        binding[CAPTURE_FIELD] = True
    if learning is not None:
        binding[LEARNING_FIELD] = learning
    elif same_project and previous and previous.get(LEARNING_FIELD) is True:
        binding[LEARNING_FIELD] = True
    path = write_binding(Path.cwd(), binding)
    # A rebind can change both the project and capture consent; Codex's
    # AGENTS.md notice is rewritten from the new binding without the engine.
    from beakr_cli.codex_context import sync_capture_notice

    sync_capture_notice()

    def render(_: dict[str, Any]) -> None:
        if previous and previous.get("project_id") != binding["project_id"]:
            err_console.print(
                f"[dim]Replaced binding to {escape(str(previous.get('name')))}.[/dim]"
            )
        console.print(
            f"Bound {escape(str(Path.cwd()))} to [bold]{escape(str(binding['name']))}[/bold] "
            f"({escape(str(binding['project_id']))})."
        )
        console.print(f"[dim]{escape(str(path))}[/dim]")
        if binding.get(CAPTURE_FIELD) is True:
            console.print("Session capture is on for this project.")
        else:
            console.print(
                "[dim]Session capture is off here; turn it on with `beakr hooks capture on`.[/dim]"
            )

    emit({**binding, "path": str(path)}, json_out=json_out, render=render)


def register(root: typer.Typer) -> None:
    project_knowledge.register(app)
    project_profile.register(app)
    root.add_typer(app, name="projects")
    root.command("init")(init_command)
