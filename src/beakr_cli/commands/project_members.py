"""``beakr projects members ls|add|rm|invite``: who can work in a project.

These live beside ``projects.py`` (already near its size budget) and attach to
the same ``projects`` Typer app. A project is named by id, slug or name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import typer

from beakr_cli.commands.projects import _resolve_project_ref
from beakr_cli.commands.projects import app as projects_app
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_binding import resolve_project
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
def list_project_members(
    project: str | None = typer.Argument(
        None, help="Project id, slug or name (or pass -P). Default: the bound project."
    ),
    project_opt: str | None = typer.Option(
        None, "--project", "-P", "--scope", help="Project id, slug or name."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Members of a project and their roles (including access inherited from a group)."""
    # -P like every other command; the positional stays for existing scripts.
    if project and project_opt and project != project_opt:
        raise typer.BadParameter("Give the project once: as an argument or with -P.")
    ref = project or project_opt or resolve_project(None)
    if not ref:
        raise typer.BadParameter(
            "Name the project (an argument or -P), or bind this directory with `beakr init`."
        )
    project = ref
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
    role: str | None = typer.Option(
        None,
        "--role",
        help=(
            "member, editor, viewer or owner. Default: member for someone new; "
            "an existing member keeps their role."
        ),
    ),
    json_out: bool = _json_opt,
) -> None:
    """Give an organization member a role in the project.

    Members are people (or service accounts); the server refuses group
    memberships, so there is no --type to ask for one. Without --role no role
    is sent: the server makes someone new a member and leaves an existing
    member's role as it is (re-adding the last owner used to demote them).
    """
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        result = sdk.projects.add_member(str(found["id"]), principal_id, role=role)
    emit(
        result,
        json_out=json_out,
        render=lambda r: console.print(
            f"Added {principal_id} to {found.get('name') or project} as "
            f"{role or (r or {}).get('role') or 'member'}.",
            highlight=False,
            markup=False,
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
    """Remove a member's access to the project.

    Safe to repeat: someone who is not a member is reported (``removed: false``)
    and the command still succeeds.
    """
    with get_sdk() as sdk:
        found = _resolve_project_ref(sdk, project)
        name = found.get("name") or project
        confirm_or_require_yes(f"Remove {principal_id} from {name}?", yes=yes)
        # The server's DELETE answers 204 whether or not the membership existed,
        # so only the member list can say whether this removes anyone.
        members = sdk.projects.members(str(found["id"]))
        sdk.projects.remove_member(str(found["id"]), principal_id, principal_type=principal_type)
    direct, inherited = _membership(members, principal_id, principal_type)
    result: dict[str, Any] = {
        "project_id": str(found.get("id")),
        "principal_id": principal_id,
        "principal_type": principal_type,
        "removed": direct,
    }
    if inherited:
        result["inherited_from"] = inherited

    def render(data: dict[str, Any]) -> None:
        if data["removed"]:
            err_console.print(f"Removed {principal_id}.", markup=False)
        elif inherited:
            err_console.print(
                f"{principal_id} has access to {name} only through {inherited}, so there is "
                "no project membership to remove; take them out of that label instead.",
                markup=False,
            )
        else:
            err_console.print(
                f"{principal_id} is not a member of {name}; nothing to remove.", markup=False
            )

    def lines(data: dict[str, Any]) -> list[str]:
        # Piped, the outcome is a line on stdout (as `members rm` prints), not
        # only the sentence on stderr.
        line = f"{data['principal_id']}\tremoved={'true' if data['removed'] else 'false'}"
        if data.get("inherited_from"):
            line += f"\tinherited_from={data['inherited_from']}"
        return [line]

    emit(result, json_out=json_out, render=render, lines=lines)


def _membership(
    members: Iterable[Mapping[str, Any]], principal_id: str, principal_type: str
) -> tuple[bool, str | None]:
    """Whether the principal holds a membership `rm` removes, and any label it inherits from.

    Access through a label is listed too (``inherited_from``), but it is not a
    project membership: removing the principal from the project leaves it.
    """
    wanted = principal_id.strip().lower()
    inherited = None
    for member in members:
        if str(member.get("principal_id") or "").lower() != wanted:
            continue
        if member.get("inherited_from"):
            inherited = inherited or str(member["inherited_from"])
        elif (member.get("principal_type") or "user") == principal_type:
            return True, None
    return False, inherited


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
            f"Invited {email} to {found.get('name') or project}.", highlight=False, markup=False
        ),
    )


def register(root: typer.Typer) -> None:
    """Attach ``beakr projects members`` (the root is unused)."""
    projects_app.add_typer(app, name="members")
