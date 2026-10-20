"""``beakr external-users ls|create|show|edit|rm``: your product's users in Beakr.

Embedded mode: your backend creates one external user per person of your
product (idempotent on your own ``external_id``), puts it in a label to give it
projects (``beakr labels members add``), and mints it short-lived tokens
(``beakr end-user-tokens mint --external-user``).

An ``external_id`` is unique only within the service account that created the
user, so two backends may each have a "customer-42". Every command that acts on
one user therefore takes its ``user_id`` (the uuid ``create`` and ``ls``
print), never an ``external_id``; ``ls --external-id`` finds the one your
credential created.
"""

from __future__ import annotations

import json
import re
from typing import Any

import typer
from rich.markup import escape

from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Manage external users: the people of your product (embedded mode).")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_org_opt = typer.Option(None, "--org", help="Organization id (default: your active org).")
_metadata_opt = typer.Option(
    None, "--metadata", help="Your own key-value data as a JSON object (at most 16 KB)."
)


_USER_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def user_id_argument(value: str) -> str:
    """An external user's ``user_id``; an ``external_id`` is refused with how to find it.

    External ids are unique per creating service account, not per organization,
    so one names no user on its own.
    """
    if not _USER_ID.match(value.strip()):
        raise typer.BadParameter(
            f"{value!r} is not a user id. External users are addressed by their user_id "
            f"(a uuid); find it with `beakr external-users ls --external-id {value}`."
        )
    return value.strip()


def _metadata(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"Not JSON: {exc}", param_hint="--metadata") from exc
    if not isinstance(value, dict):
        raise typer.BadParameter('Pass a JSON object, e.g. \'{"plan": "pro"}\'.')
    return value


def _render_one(user: dict[str, Any]) -> None:
    console.print(
        f"[bold]{escape(str(user.get('name') or user.get('external_id')))}[/bold] "
        f"({escape(str(user.get('user_id')))})"
    )
    for label, key in (
        ("External id", "external_id"),
        ("Active", "active"),
        ("Created", "created_at"),
    ):
        if user.get(key) is not None:
            console.print(f"{label + ':':<13}{user[key]}", highlight=False, markup=False)
    if user.get("metadata"):
        console.print(
            f"{'Metadata:':<13}{json.dumps(user['metadata'])}", highlight=False, markup=False
        )


@app.command("ls")
def list_external_users(
    external_id: str | None = typer.Option(
        None,
        "--external-id",
        help="Find the one your credential created with this id from your system.",
    ),
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """List the organization's external users."""
    with get_sdk() as sdk:
        users = sdk.external_users.list(external_id=external_id, org_id=org)

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No external users. Create one with `beakr external-users create`.")
            return
        print_table(
            ["ID", "External id", "Name", "Active", "Created"],
            [
                [
                    str(u.get("user_id") or ""),
                    short(u.get("external_id"), 32),
                    short(u.get("name"), 28),
                    "yes" if u.get("active") else "no",
                    str(u.get("created_at") or "")[:10],
                ]
                for u in rows
            ],
            title="External users",
        )

    emit(users, json_out=json_out, render=render)


@app.command("create")
def create_external_user(
    external_id: str = typer.Argument(..., help="Your own id for this person."),
    name: str | None = typer.Option(None, "--name", help="Display name (default: the id)."),
    metadata: str | None = _metadata_opt,
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Create an external user, or return the existing one with that external id."""
    with get_sdk() as sdk:
        user = sdk.external_users.create(
            external_id, name=name, metadata=_metadata(metadata), org_id=org
        )

    def render(u: dict[str, Any]) -> None:
        verb = "Created" if u.get("created") else "Already existed:"
        console.print(
            f"{verb} external user {u.get('external_id')} ({u.get('user_id')})", markup=False
        )

    emit(
        user,
        json_out=json_out,
        render=render,
        hint=f"beakr labels members add LABEL {user.get('user_id')}",
    )


@app.command("show")
def show_external_user(
    user_id: str = typer.Argument(
        ..., help="The external user's user_id (from `ls`).", callback=user_id_argument
    ),
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Show one external user."""
    with get_sdk() as sdk:
        user = sdk.external_users.get(user_id, org_id=org)
    emit(user, json_out=json_out, render=_render_one)


@app.command("edit")
def update_external_user(
    user_id: str = typer.Argument(
        ..., help="The external user's user_id (from `ls`).", callback=user_id_argument
    ),
    name: str | None = typer.Option(None, "--name", help="New display name."),
    metadata: str | None = _metadata_opt,
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Change an external user's name or metadata."""
    if name is None and metadata is None:
        raise typer.BadParameter("Pass --name and/or --metadata.")
    with get_sdk() as sdk:
        user = sdk.external_users.update(
            user_id, name=name, metadata=_metadata(metadata), org_id=org
        )
    emit(user, json_out=json_out, render=_render_one)


@app.command("rm")
def delete_external_user(
    user_id: str = typer.Argument(
        ..., help="The external user's user_id (from `ls`).", callback=user_id_argument
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Delete an external user: its personal data is erased; what it added to projects stays."""
    confirm_or_require_yes(f"Delete external user {user_id}?", yes=yes)
    with get_sdk() as sdk:
        sdk.external_users.delete(user_id, org_id=org)
    emit(
        {"user_id": user_id, "deleted": True},
        json_out=json_out,
        render=lambda d: err_console.print(f"Deleted external user {d['user_id']}.", markup=False),
        json_when_piped=False,
    )


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="external-users")
