"""``beakr profile``: the evidence-backed profile agents use, and project profiles.

Shared (project and organization) profile claims are not in this release; a
project's or the organization's profile document is still shown and started here.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape

from beakr_cli.output import console, emit, err_console, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes

app = typer.Typer(
    help="Show and correct your profile, and show a project's or the organization's profile."
)

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


def _shared_scope(project: str | None, org: bool) -> dict[str, Any] | None:
    """The project or organization profile a command targets, or None for yours."""
    if project and org:
        err_console.print("Use either --project or --org, not both.")
        raise typer.Exit(2)
    if project:
        return {"project_id": project}
    if org:
        return {}
    return None


def _render_claims(claims: list[dict[str, Any]]) -> None:
    if not claims:
        err_console.print("No profile claims yet.")
        return
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        grouped[
            str(claim.get("profile_layer") or claim.get("section") or claim.get("layer") or "other")
        ].append(claim)
    for section, items in grouped.items():
        console.print(f"\n[bold]{escape(str(section))}[/bold]")
        for claim in items:
            state = claim.get("confirmation_state") or ""
            status = claim.get("status") or ""
            marks = ", ".join(value for value in (state, status) if value)
            console.print(
                f"  {escape(short(claim.get('content'), 90))}  [dim]({escape(marks)}) "
                f"{escape(str(claim.get('id')))}[/dim]",
                highlight=False,
            )


def _render_project_profile(profile: dict[str, Any]) -> None:
    console.print(f"[bold]{escape(str(profile.get('name') or 'Project profile'))}[/bold]")
    if profile.get("description"):
        console.print(profile["description"], highlight=False, markup=False)
    for label, key in (("Goals", "goals"), ("Focus areas", "focus_areas")):
        values = profile.get(key) or []
        if values:
            console.print(
                f"{label}: " + ", ".join(str(v) for v in values), highlight=False, markup=False
            )
    for label, key in (
        ("Key people", "core_people"),
        ("Key organizations", "core_organizations"),
        ("Key topics", "core_topics"),
    ):
        values = profile.get(key) or []
        if values:
            console.print(f"{label}:", markup=False)
            for item in values:
                name = item.get("name") if isinstance(item, dict) else item
                console.print(f"  - {name}", highlight=False, markup=False)


@app.command("show")
def show(
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Show this project's profile instead of yours.",
        callback=project_ref_callback,
    ),
    org: bool = typer.Option(False, "--org", help="The organization's profile instead of yours."),
    include_inactive: bool = typer.Option(
        False, "--all", "-a", help="Include dismissed and retired claims."
    ),
    layer: list[str] = typer.Option(  # noqa: B008 - typer reads the default
        [],
        "--layer",
        "-l",
        help=(
            "Only this layer of your profile: identity, goals, arcs, reminders, "
            "preferences or patterns (or a stored layer such as care_about). Repeatable."
        ),
    ),
    json_out: bool = _json_opt,
) -> None:
    """Show your profile claims, grouped by layer, or a project's or the organization's profile."""
    from beakr_cli.sdk import get_sdk

    scope = _shared_scope(project, org)
    if layer and scope is not None:
        raise typer.BadParameter("--layer filters your own profile; leave out --project and --org.")
    with get_sdk() as sdk:
        if scope is not None:
            document = sdk.profile.project(project) if project else sdk.profile.org()
            if json_out:
                emit({"profile": document}, json_out=True)
                return
            if document:
                _render_project_profile(document)
            else:
                name = f"Project {project}" if project else "The organization"
                err_console.print(f"{name} has no knowledge profile yet.", markup=False)
            return
        claims = sdk.profile.claims(include_inactive=include_inactive or None, layer=layer or None)
    emit(claims, json_out=json_out, render=_render_claims)


@app.command("create")
def create(
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Start this project's knowledge profile.",
        callback=project_ref_callback,
    ),
    org: bool = typer.Option(False, "--org", help="Start the organization's knowledge profile."),
    description: str = typer.Option("", "--description", "-d", help="What it is about."),
    goal: list[str] = typer.Option([], "--goal", help="A goal; repeatable."),
    focus: list[str] = typer.Option([], "--focus", help="A focus area; repeatable."),
    json_out: bool = _json_opt,
) -> None:
    """Start a project's or the organization's knowledge profile (its owners only)."""
    from beakr_cli.sdk import get_sdk

    scope = _shared_scope(project, org)
    if scope is None:
        err_console.print("Name the profile: --project ID or --org.")
        raise typer.Exit(2)
    with get_sdk() as sdk:
        created = sdk.profile.create_profile(
            **scope, description=description, goals=list(goal), focus_areas=list(focus)
        )
    emit(
        created,
        json_out=json_out,
        render=lambda p: console.print(
            f"Created {p.get('profile_type', 'profile')} profile {p.get('id')}.", markup=False
        ),
    )


@app.command("confirm")
def confirm(
    claim_id: str = typer.Argument(..., help="Claim ID."),
    json_out: bool = _json_opt,
) -> None:
    """Confirm one of your claims is right."""
    from beakr_cli.sdk import get_sdk

    with get_sdk() as sdk:
        claim = sdk.profile.confirm(claim_id)
    emit(
        claim,
        json_out=json_out,
        render=lambda c: console.print(f"Confirmed {claim_id}.", markup=False),
    )


@app.command("dismiss")
def dismiss(
    claim_id: str = typer.Argument(..., help="Claim ID."),
    json_out: bool = _json_opt,
) -> None:
    """Mark one of your claims wrong; it stays visible as dismissed and is not relearned."""
    from beakr_cli.sdk import get_sdk

    with get_sdk() as sdk:
        claim = sdk.profile.dismiss(claim_id)
    emit(
        claim,
        json_out=json_out,
        render=lambda c: console.print(f"Dismissed {claim_id}.", markup=False),
    )


@app.command("edit")
def edit(
    claim_id: str = typer.Argument(..., help="Claim ID."),
    content: str | None = typer.Option(None, "--content", "-c", help="New wording of the claim."),
    status: str | None = typer.Option(
        None, "--status", help="Status of a current-work or commitment claim, e.g. done."
    ),
    next_step: str | None = typer.Option(None, "--next-step", help="What happens next."),
    json_out: bool = _json_opt,
) -> None:
    """Correct one of your claims: its wording, status or next step."""
    from beakr_cli.sdk import get_sdk

    if content is None and status is None and next_step is None:
        raise typer.BadParameter("Give at least one of --content, --status or --next-step.")
    with get_sdk() as sdk:
        claim = sdk.profile.edit(claim_id, content=content, status=status, next_step=next_step)
    emit(
        claim,
        json_out=json_out,
        render=lambda c: console.print(f"Updated {claim_id}.", markup=False),
    )


_CHANGE_ACTIONS = ("confirm", "dismiss", "edit", "restore")


def _read_changes(file: str) -> list[dict[str, Any]]:
    """The ``changes`` list from a JSON file (``-`` for stdin).

    Accepts the request body itself (``{"changes": [...]}``) or the bare list.
    """
    try:
        text = sys.stdin.read() if file == "-" else Path(file).read_text()
    except OSError as exc:
        err_console.print(f"Cannot read {file}: {exc.strerror or exc}.", markup=False)
        raise typer.Exit(2) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        err_console.print(f"{file} is not JSON: {exc.msg} (line {exc.lineno}).", markup=False)
        raise typer.Exit(2) from exc
    changes = data.get("changes") if isinstance(data, dict) else data
    if not isinstance(changes, list) or not changes:
        err_console.print(
            'The file must hold {"changes": [...]} or a list of changes, at least one.'
        )
        raise typer.Exit(2)
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or not change.get("claim_id"):
            err_console.print(f"Change {index} needs a claim_id.", markup=False)
            raise typer.Exit(2)
        if change.get("action") not in _CHANGE_ACTIONS:
            actions = ", ".join(_CHANGE_ACTIONS)
            err_console.print(f"Change {index}: action must be one of {actions}.", markup=False)
            raise typer.Exit(2)
    return changes


def _render_applied(result: dict[str, Any], changes: list[dict[str, Any]]) -> None:
    counts = Counter(str(change["action"]) for change in changes)
    done = ", ".join(f"{counts[action]} {action}" for action in _CHANGE_ACTIONS if counts[action])
    console.print(f"Applied {len(changes)} change(s): {done}.", markup=False)
    _render_claims(result.get("claims") or [])


@app.command("apply")
def apply(
    file: str = typer.Option(
        ...,
        "--file",
        "-f",
        help=(
            'JSON file of changes ("-" for stdin): {"changes": [{"claim_id": ..., '
            '"action": "confirm|dismiss|edit|restore", "content": ...}]} or the list alone.'
        ),
    ),
    json_out: bool = _json_opt,
) -> None:
    """Review many of your claims at once: all the changes apply, or none do.

    Each change confirms, dismisses, edits (content, scope, status, next_step) or
    restores one claim, as `beakr profile confirm|dismiss|edit|restore` would.
    Up to 100 changes, each claim at most once. If any change is refused, the
    error names it and nothing is changed.
    """
    from beakr_cli.sdk import get_sdk

    changes = _read_changes(file)
    with get_sdk() as sdk:
        result = sdk.profile.apply(changes)
    emit(result, json_out=json_out, render=lambda r: _render_applied(r, changes))


@app.command("forget")
def forget(
    claim_id: str = typer.Argument(..., help="Claim ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    json_out: bool = _json_opt,
) -> None:
    """Erase a claim permanently (it will not be relearned)."""
    from beakr_cli.sdk import get_sdk

    confirm_or_require_yes(f"Permanently erase claim {claim_id}?", yes)
    with get_sdk() as sdk:
        sdk.profile.forget(claim_id)
    emit(
        {"forgotten": claim_id},
        json_out=json_out,
        render=lambda _: console.print(f"Forgot {claim_id}.", markup=False),
    )


@app.command("restore")
def restore(
    claim_id: str = typer.Argument(..., help="Claim ID."),
    json_out: bool = _json_opt,
) -> None:
    """Undo a confirm or dismiss: the claim goes back to review."""
    from beakr_cli.sdk import get_sdk

    with get_sdk() as sdk:
        claim = sdk.profile.restore(claim_id)
    emit(
        claim,
        json_out=json_out,
        render=lambda c: console.print(f"Restored {claim_id}.", markup=False),
    )


def register(root: typer.Typer) -> None:
    """Attach ``beakr profile ...``."""
    root.add_typer(app, name="profile")
