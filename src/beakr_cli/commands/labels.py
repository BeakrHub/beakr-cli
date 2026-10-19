"""``beakr labels``: named groups of projects with their own members.

A label gives its members read access to every project attached to it, which
is how an embedded product hands an external user its projects::

    beakr labels create "Acme customers"
    beakr labels projects add "Acme customers" acme-docs
    beakr labels members add "Acme customers" USER_ID

A label is named by its id or its exact name; a project by id, slug or name.
"""

from __future__ import annotations

from typing import Any

import typer
from beakr_sdk.errors import NotFoundError

from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_ref import resolve_project_ref
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Manage labels: groups of projects with their own members.")
members_app = typer.Typer(help="People and external users in a label.")
projects_app = typer.Typer(help="Projects a label gives its members.")
app.add_typer(members_app, name="members")
app.add_typer(projects_app, name="projects")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_label_arg = typer.Argument(..., help="Label id or exact name.")


def _label_id(sdk: Any, ref: str) -> str:
    """The id of the label ``ref`` names (an id, or a case-insensitive exact name)."""
    labels = sdk.labels.list() or []
    for label in labels:
        if str(label.get("id")) == ref:
            return ref
    wanted = ref.casefold()
    matches = [label for label in labels if str(label.get("name") or "").casefold() == wanted]
    if len(matches) == 1:
        return str(matches[0]["id"])
    if matches:
        raise typer.BadParameter(f"Several labels are named {ref!r}; pass its id.")
    names = ", ".join(str(label.get("name")) for label in labels[:10]) or "none yet"
    # Not found (exit 4), as a missing project, page or API object is: a label
    # that does not exist is not a mistyped command line.
    raise NotFoundError(f"No label with id or name {ref!r}. Labels: {names}.")


def _render_labels(rows: list[dict[str, Any]]) -> None:
    if not rows:
        err_console.print("No labels. Create one with `beakr labels create NAME`.")
        return
    print_table(
        ["ID", "Name", "Members", "Description"],
        [
            [
                str(label.get("id") or ""),
                short(label.get("name"), 32),
                str(label.get("member_count") if label.get("member_count") is not None else ""),
                short(label.get("description"), 40),
            ]
            for label in rows
        ],
        title="Labels",
    )


def _render_label(label: dict[str, Any]) -> None:
    console.print(f"[bold]{label.get('name')}[/bold] ({label.get('id')})")
    if label.get("description"):
        console.print(str(label["description"]))
    if label.get("member_count") is not None:
        console.print(f"Members: {label['member_count']}")


@app.command("ls")
def list_labels(json_out: bool = _json_opt) -> None:
    """List the organization's labels."""
    with get_sdk() as sdk:
        labels = sdk.labels.list()
    emit(labels, json_out=json_out, render=_render_labels)


@app.command("create")
def create_label(
    name: str = typer.Argument(..., help="Label name."),
    description: str | None = typer.Option(None, "--description", "-d"),
    color: str | None = typer.Option(None, "--color", help="Display color, e.g. #3b82f6."),
    json_out: bool = _json_opt,
) -> None:
    """Create a label."""
    with get_sdk() as sdk:
        label = sdk.labels.create(name, description=description, color=color)
    emit(
        label,
        json_out=json_out,
        render=_render_label,
        hint=f"beakr labels projects add {label.get('id')} PROJECT",
    )


@app.command("show")
def show_label(label: str = _label_arg, json_out: bool = _json_opt) -> None:
    """Show a label."""
    with get_sdk() as sdk:
        data = sdk.labels.get(_label_id(sdk, label))
    emit(data, json_out=json_out, render=_render_label)


@app.command("edit")
def update_label(
    label: str = _label_arg,
    name: str | None = typer.Option(None, "--name", help="New name."),
    description: str | None = typer.Option(None, "--description", "-d"),
    color: str | None = typer.Option(None, "--color"),
    json_out: bool = _json_opt,
) -> None:
    """Rename a label or change its description or color."""
    fields = {
        key: value
        for key, value in (("name", name), ("description", description), ("color", color))
        if value is not None
    }
    if not fields:
        raise typer.BadParameter("Pass --name, --description or --color.")
    with get_sdk() as sdk:
        data = sdk.labels.update(_label_id(sdk, label), **fields)
    emit(data, json_out=json_out, render=_render_label)


@app.command("rm")
def delete_label(
    label: str = _label_arg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    json_out: bool = _json_opt,
) -> None:
    """Delete a label: its members lose the access it gave them."""
    with get_sdk() as sdk:
        label_id = _label_id(sdk, label)
        confirm_or_require_yes(f"Delete label {label}? Its members lose its projects.", yes=yes)
        sdk.labels.delete(label_id)
    emit(
        {"id": label_id, "deleted": True},
        json_out=json_out,
        render=lambda d: err_console.print(f"Deleted label {d['id']}."),
        json_when_piped=False,
    )


@members_app.command("ls")
def list_label_members(label: str = _label_arg, json_out: bool = _json_opt) -> None:
    """Who is in a label."""
    with get_sdk() as sdk:
        members = sdk.labels.members(_label_id(sdk, label))

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No members.")
            return
        print_table(
            ["User ID", "Name", "Email or external id", "Kind", "Role"],
            [
                [
                    str(m.get("user_id") or ""),
                    short(m.get("display_name"), 28),
                    short(m.get("email") or m.get("external_id"), 32),
                    str(m.get("principal_kind") or ""),
                    str(m.get("role") or ""),
                ]
                for m in rows
            ],
        )

    emit(members, json_out=json_out, render=render)


@members_app.command("add")
def add_label_member(
    label: str = _label_arg,
    user_id: str = typer.Argument(..., help="User id of a member or an external user."),
    json_out: bool = _json_opt,
) -> None:
    """Add a person or an external user to a label."""
    with get_sdk() as sdk:
        member = sdk.labels.add_member(_label_id(sdk, label), user_id)
    emit(
        member,
        json_out=json_out,
        render=lambda m: console.print(f"Added {m.get('user_id')} to the label."),
    )


@members_app.command("rm")
def remove_label_member(
    label: str = _label_arg,
    user_id: str = typer.Argument(..., help="User id to remove."),
    json_out: bool = _json_opt,
) -> None:
    """Remove someone from a label."""
    with get_sdk() as sdk:
        label_id = _label_id(sdk, label)
        sdk.labels.remove_member(label_id, user_id)
    emit(
        {"label_id": label_id, "user_id": user_id, "removed": True},
        json_out=json_out,
        render=lambda d: err_console.print(f"Removed {d['user_id']} from the label."),
        json_when_piped=False,
    )


@projects_app.command("ls")
def list_label_projects(label: str = _label_arg, json_out: bool = _json_opt) -> None:
    """Projects a label gives its members."""
    with get_sdk() as sdk:
        attached = sdk.labels.projects(_label_id(sdk, label))

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No projects. Attach one with `beakr labels projects add`.")
            return
        print_table(
            ["Project ID", "Name", "Role"],
            [
                [
                    str(p.get("project_id") or ""),
                    short((p.get("project") or {}).get("name"), 40),
                    str(p.get("role") or ""),
                ]
                for p in rows
            ],
        )

    emit(attached, json_out=json_out, lines=_project_lines, render=render)


def _project_lines(rows: list[dict[str, Any]]) -> list[str]:
    return [
        "\t".join(
            [
                str(p.get("project_id") or ""),
                str((p.get("project") or {}).get("name") or ""),
                f"role={p.get('role')}",
            ]
        )
        for p in rows
    ]


@projects_app.command("add")
def attach_label_project(
    label: str = _label_arg,
    project: str = typer.Argument(..., help="Project id, slug or name."),
    json_out: bool = _json_opt,
) -> None:
    """Give a label's members read access to a project."""
    project_id = resolve_project_ref(project, param_hint="PROJECT")
    with get_sdk() as sdk:
        attached = sdk.labels.attach_project(_label_id(sdk, label), project_id)
    emit(
        attached,
        json_out=json_out,
        lines=lambda p: _project_lines([p]),
        render=lambda p: console.print(f"Attached project {p.get('project_id')} to the label."),
    )


@projects_app.command("rm")
def detach_label_project(
    label: str = _label_arg,
    project: str = typer.Argument(..., help="Project id, slug or name."),
    json_out: bool = _json_opt,
) -> None:
    """Take a project away from a label's members."""
    project_id = resolve_project_ref(project, param_hint="PROJECT")
    with get_sdk() as sdk:
        label_id = _label_id(sdk, label)
        sdk.labels.detach_project(label_id, project_id)
    emit(
        {"label_id": label_id, "project_id": project_id, "detached": True},
        json_out=json_out,
        render=lambda d: err_console.print(f"Detached project {d['project_id']}."),
        json_when_piped=False,
    )


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="labels")
