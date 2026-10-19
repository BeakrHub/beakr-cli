"""``beakr projects members ls|add|rm|invite``: who can work in a project.

These live beside ``projects.py`` (already near its size budget) and attach to
the same ``projects`` Typer app. A project is named by id, slug or name.
"""

from __future__ import annotations

from typing import Any

import typer

from beakr_cli.commands.projects import _resolve_project_ref
from beakr_cli.commands.projects import app as projects_app
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Members of a project: list, add, remove, invite by email.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_project_arg = typer.Argument(..., help="Project id, slug or name.")
# Only `rm` takes a type: the server no longer adds group memberships, but a
# project can still carry one from before, and removing it names its type.
_principal_type_opt = typer.Option(
    "user", "--type", help="user, or group to remove an existing group membership."
)


@app.command("ls")
def list_project_members(project: str = _project_arg, json_out: bool = _json_opt) -> None:
    """Members of a project and their roles (including access inherited from a group)."""
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        members = sdk.projects.members(str(found["id"]))

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No members.")
            return
        print_table(
            ["Name", "Email", "Role", "ID", "Via"],
            [
                [
                    short(m.get("name"), 28),
                    short(m.get("email"), 32),
                    str(m.get("role") or ""),
                    str(m.get("principal_id") or ""),
                    short(m.get("inherited_from") or m.get("principal_type"), 16),
                ]
                for m in rows
            ],
            title=f"Members of {found.get('name') or project}",
        )

    emit(members, json_out=json_out, render=render)


@app.command("add")
def add_project_member(
    project: str = _project_arg,
    principal_id: str = typer.Argument(..., help="User id (from `beakr members ls`)."),
    role: str = typer.Option("member", "--role", help="member, editor, viewer or owner."),
    json_out: bool = _json_opt,
) -> None:
    """Give an organization member a role in the project.

    Members are people (or service accounts); the server refuses group
    memberships, so there is no --type to ask for one.
    """
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        result = sdk.projects.add_member(str(found["id"]), principal_id, role=role)
    emit(
        result,
        json_out=json_out,
        render=lambda _: console.print(
            f"Added {principal_id} to {found.get('name') or project} as {role}.", highlight=False
        ),
    )


@app.command("rm")
def remove_project_member(
    project: str = _project_arg,
    principal_id: str = typer.Argument(..., help="User or group id."),
    principal_type: str = _principal_type_opt,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    json_out: bool = _json_opt,
) -> None:
    """Remove a member's access to the project."""
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        confirm_or_require_yes(
            f"Remove {principal_id} from {found.get('name') or project}?", yes=yes
        )
        sdk.projects.remove_member(str(found["id"]), principal_id, principal_type=principal_type)
    emit(
        {
            "project_id": str(found.get("id")),
            "principal_id": principal_id,
            "principal_type": principal_type,
            "removed": True,
        },
        json_out=json_out,
        render=lambda _: err_console.print(f"Removed {principal_id}."),
        json_when_piped=False,
    )


@app.command("invite")
def invite_to_project(
    project: str = _project_arg,
    email: str = typer.Argument(..., help="Who to invite."),
    role: str = typer.Option("member", "--role", help="Their role once they accept."),
    expires_in_days: int | None = typer.Option(None, "--expires-in-days", min=1, max=90),
    json_out: bool = _json_opt,
) -> None:
    """Invite someone by email to join the project."""
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        result = sdk.projects.invite(
            str(found["id"]), email, role=role, expires_in_days=expires_in_days
        )
    emit(
        result,
        json_out=json_out,
        render=lambda _: console.print(
            f"Invited {email} to {found.get('name') or project}.", highlight=False
        ),
    )


def register(root: typer.Typer) -> None:
    """Attach ``beakr projects members`` (the root is unused)."""
    projects_app.add_typer(app, name="members")
