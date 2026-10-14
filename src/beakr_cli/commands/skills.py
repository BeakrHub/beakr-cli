"""``beakr skills``: your Beakr skill library, and syncing it with local agents.

Every command works on your own library. Project, organization and
integration skills (listing them by scope, creating, changing and reviewing
them) are not in this release.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer

from beakr_cli._private_api.resources.skills import SKILL_MD
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.sdk import get_sdk
from beakr_cli.skills_sync import local_status, sync_skills

app = typer.Typer(help="List, read and sync Beakr skills with Claude Code and Codex.")

_json_opt = typer.Option(False, "--json", "-j", help="Output JSON.")
_DIRECTIONS = ("pull", "push", "both")
_TARGETS = ("claude", "codex", "both")


@app.command("ls")
def list_skills(json_out: bool = _json_opt) -> None:
    """Skills in your library, with whether each is synced to Claude Code and Codex."""
    with get_sdk() as sdk:
        skills = sdk.skills.list()
    rows = [{**skill, "local": local_status(str(skill.get("slug") or ""))} for skill in skills]

    def render(items: list[dict[str, Any]]) -> None:
        if not items:
            err_console.print("No skills in your library.")
            return
        print_table(
            ["Slug", "Name", "Owner", "Claude", "Codex", "Description"],
            [
                [
                    short(item.get("slug"), 28),
                    short(item.get("name"), 28),
                    short(item.get("owner_type"), 8),
                    item["local"]["claude"],
                    item["local"]["codex"],
                    short(item.get("description"), 40),
                ]
                for item in items
            ],
        )

    emit(rows, json_out=json_out, render=render)


def _skill_id(sdk: Any, skill: str) -> str:
    """A library skill's id from its slug or id (an unknown value passes through)."""
    match = next(
        (item for item in sdk.skills.list() if skill in (str(item.get("id")), item.get("slug"))),
        None,
    )
    return str(match["id"]) if match else skill


@app.command("show")
def show_skill(
    skill: str = typer.Argument(..., help="Skill slug or id."),
    json_out: bool = _json_opt,
) -> None:
    """Print a skill's SKILL.md."""
    with get_sdk() as sdk:
        bundle = sdk.skills.bundle(_skill_id(sdk, skill))
    if json_out:
        emit(bundle, json_out=True)
        return
    text = str((bundle.get("files") or {}).get("SKILL.md", ""))
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


@app.command("sync")
def sync(
    direction: str = typer.Option("both", "--direction", "-d", help="pull, push or both."),
    target: str = typer.Option("both", "--target", "-t", help="claude, codex or both."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would change."),
    force: bool = typer.Option(
        False, "--force", help="Resolve conflicts in favour of the side being written."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Sync your skill library to ~/.claude/skills and ~/.codex/skills, and local edits back."""
    if direction not in _DIRECTIONS:
        err_console.print(f"Invalid --direction '{direction}'. Choose: {', '.join(_DIRECTIONS)}.")
        raise typer.Exit(2)
    if target not in _TARGETS:
        err_console.print(f"Invalid --target '{target}'. Choose: {', '.join(_TARGETS)}.")
        raise typer.Exit(2)
    with get_sdk() as sdk:
        actions = sync_skills(
            sdk,
            direction=direction,
            target=target,
            dry_run=dry_run,
            force=force,
        )
    rows = [action.as_dict() for action in actions]

    def render(items: list[dict[str, str]]) -> None:
        changed = [item for item in items if item["action"] != "unchanged"]
        if not changed:
            console.print("Everything is in sync.")
            return
        prefix = "Would apply" if dry_run else "Applied"
        print_table(
            ["Skill", "Target", "Action", "Detail"],
            [[i["slug"], i["target"], i["action"], short(i["detail"], 60)] for i in changed],
            title=f"{prefix} skill sync",
        )

    emit(rows, json_out=json_out, render=render)
    if any(action.action == "conflict" for action in actions):
        raise typer.Exit(5)


@app.command("edit")
def edit_skill(
    skill: str = typer.Argument(..., help="Skill slug or id."),
    file: Path = typer.Option(
        ..., "--file", "-f", exists=True, dir_okay=False, help="Local file with the new content."
    ),
    path: str = typer.Option(
        SKILL_MD, "--path", help="File in the skill's bundle to replace (default SKILL.md)."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Replace one file of a skill in your library (SKILL.md by default) with a local file."""
    content = file.read_text(encoding="utf-8")
    with get_sdk() as sdk:
        found = next(
            (s for s in sdk.skills.list() if skill in (str(s.get("id")), s.get("slug"))), None
        )
        if found is not None and found.get("owner_type") in ("org", "project"):
            err_console.print(
                f"'{skill}' is a {found['owner_type']} skill; changing shared skills is parked."
            )
            raise typer.Exit(2)
        result = sdk.skills.put_file(str(found["id"]) if found else skill, path, content)
    message = f"Updated {path} of {skill}."
    emit(result, json_out=json_out, render=lambda _: console.print(message, highlight=False))


@app.command("proposals")
def list_proposals(
    status: str | None = typer.Option(None, "--status", help="e.g. pending, accepted."),
    json_out: bool = _json_opt,
) -> None:
    """Skills Beakr noticed in your work and proposes you keep."""
    with get_sdk() as sdk:
        proposals = sdk.skills.proposals.list(status=status)

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No skill proposals.")
            return
        print_table(
            ["Proposal ID", "Title", "Seen", "Status"],
            [
                [
                    str(p.get("id")),
                    short(p.get("title"), 44),
                    f"{p.get('occurrence_count') or 0}x",
                    str(p.get("status") or ""),
                ]
                for p in rows
            ],
            title=f"Skill proposals ({len(rows)})",
        )
        err_console.print("Keep one with `beakr skills approve PROPOSAL_ID`.")

    emit(proposals, json_out=json_out, render=render)


@app.command("approve")
def approve_proposal(
    proposal_id: str = typer.Argument(..., help="Proposal id from `beakr skills proposals`."),
    force: bool = typer.Option(
        False, "--force", help="Replace an existing skill with the same slug."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Install a proposed skill into your library."""
    with get_sdk() as sdk:
        result = sdk.skills.proposals.accept(proposal_id, force=force or None)
    emit(
        result,
        json_out=json_out,
        render=lambda _: console.print(
            f"Installed skill from proposal {proposal_id}. Run `beakr skills sync` to pull it."
        ),
    )


def register(root: typer.Typer) -> None:
    """Attach ``beakr skills``."""
    root.add_typer(app, name="skills")
