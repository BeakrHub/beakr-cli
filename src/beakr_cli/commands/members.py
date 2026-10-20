"""Organization member commands: ``beakr members ls|invite|role|rm``."""

from __future__ import annotations

from enum import Enum
from typing import Any

import typer

from beakr_cli.output import console, emit, print_table, short
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Manage organization members and invitations.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_org_opt = typer.Option(None, "--org", help="Organization id (default: your active org).")


class Role(str, Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


@app.command("ls")
def list_members(org: str | None = _org_opt, json_out: bool = _json_opt) -> None:
    """List members of the organization."""
    with get_sdk() as sdk:
        members = sdk.members.list(org_id=org)

    def render(rows: list[dict[str, Any]]) -> None:
        print_table(
            ["Name", "Email", "Role", "User ID", "Joined"],
            [
                [
                    short(m.get("display_name"), 28),
                    short(m.get("email"), 36),
                    str(m.get("role") or ""),
                    str(m.get("user_id") or ""),
                    str(m.get("joined_at") or "")[:10],
                ]
                for m in rows
            ],
            title="Members",
        )

    emit(members, json_out=json_out, render=render)


@app.command("invite")
def invite(
    email: str = typer.Argument(..., help="Email address to invite."),
    role: Role = typer.Option(Role.member, "--role", "-r", help="Role once accepted."),
    expires_in_days: int | None = typer.Option(None, "--expires-in-days", help="1 to 30."),
    json_out: bool = _json_opt,
) -> None:
    """Invite someone to the organization."""
    with get_sdk() as sdk:
        invitation = sdk.invitations.create(email, role=role.value, expires_in_days=expires_in_days)
    emit(
        invitation,
        json_out=json_out,
        render=lambda i: console.print(
            f"Invited {email} as {role.value} ({i.get('id')}).", markup=False
        ),
    )


@app.command("role")
def set_role(
    user_id: str = typer.Argument(..., help="User id (from beakr members ls)."),
    role: Role = typer.Argument(..., help="owner, admin, member or viewer."),
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Change a member's role."""
    with get_sdk() as sdk:
        updated = sdk.members.set_role(user_id, role.value, org_id=org)
    emit(
        updated,
        json_out=json_out,
        render=lambda _: console.print(f"{user_id} is now {role.value}.", markup=False),
    )


@app.command("rm")
def remove_member(
    user_id: str = typer.Argument(..., help="User id (from beakr members ls)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Remove a member from the organization."""
    confirm_or_require_yes(f"Remove {user_id} from the organization?", yes)
    with get_sdk() as sdk:
        sdk.members.remove(user_id, org_id=org)
    emit(
        {"user_id": user_id, "removed": True},
        json_out=json_out,
        render=lambda _: console.print(f"Removed {user_id}.", markup=False),
    )


@app.command("invitations")
def list_invitations(json_out: bool = _json_opt) -> None:
    """List pending invitations."""
    with get_sdk() as sdk:
        invitations = sdk.invitations.list()

    def render(rows: list[dict[str, Any]]) -> None:
        print_table(
            ["ID", "Email", "Expires", "Accepted"],
            [
                [
                    str(i.get("id") or ""),
                    short(i.get("invitee_email"), 36),
                    str(i.get("expires_at") or "")[:10],
                    str(i.get("accepted_at") or "-")[:10],
                ]
                for i in rows
            ],
            title="Invitations",
        )

    emit(invitations, json_out=json_out, render=render)


@app.command("revoke-invite")
def revoke_invitation(
    invitation_id: str = typer.Argument(..., help="Invitation id."),
    json_out: bool = _json_opt,
) -> None:
    """Revoke a pending invitation."""
    with get_sdk() as sdk:
        sdk.invitations.revoke(invitation_id)
    emit(
        {"id": invitation_id, "revoked": True},
        json_out=json_out,
        render=lambda _: console.print(f"Revoked invitation {invitation_id}.", markup=False),
    )


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="members")
