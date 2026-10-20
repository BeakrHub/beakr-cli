"""``beakr skills``: browse, install, write and review Beakr skills, and sync them locally.

A skill is a SKILL.md folder your agent loads on demand. ``ls`` covers your own
skills, your organization's, the built-in ones and public skills from anywhere,
each labelled with its visibility, owner (``me``, ``org``, ``system``,
``anyone``), origin (``created``, ``mined``, ``imported``, ``system``) and
whether it is installed in your library. Every command goes through the
documented skills API (``beakr_sdk``'s ``skills`` resource).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer
from beakr_sdk.resources.skills import SKILL_MD

from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk
from beakr_cli.skills_sync import local_status, sync_skills

app = typer.Typer(help="Browse, install, write and sync Beakr skills.")

_json_opt = typer.Option(False, "--json", "-j", help="Output JSON.")
_DIRECTIONS = ("pull", "push", "both")
_TARGETS = ("claude", "codex", "both")
_VISIBILITIES = ("private", "org", "public")
_ORIGINS = ("created", "mined", "imported", "system")
_skill_arg = typer.Argument(..., help="Skill id or slug.")


def _invalid(option: str, value: str, choices: tuple[str, ...]) -> typer.Exit:
    err_console.print(
        f"Invalid {option} '{value}'. Choose: {', '.join(choices)}.", markup=False, highlight=False
    )
    return typer.Exit(2)


def _yes_no(value: Any) -> str:
    return "yes" if value else "no"


@app.command("ls")
def list_skills(
    mine: bool = typer.Option(False, "--mine", help="Skills you wrote."),
    org: bool = typer.Option(False, "--org", help="Your organization's skills."),
    public: bool = typer.Option(
        False, "--public", help="Public skills from outside your organization."
    ),
    builtin: bool = typer.Option(False, "--builtin", help="Skills built into Beakr."),
    installed: bool | None = typer.Option(
        None,
        "--installed/--available",
        help="Only skills in your library, or only the ones you could install.",
        show_default=False,
    ),
    origin: list[str] = typer.Option(
        [], "--origin", help="created, mined, imported or system. Repeat for several."
    ),
    visibility: list[str] = typer.Option(
        [], "--visibility", help="private, org or public. Repeat for several."
    ),
    search: str | None = typer.Option(None, "--search", "-s", help="Words to match."),
    domain: str | None = typer.Option(None, "--domain", help="Only skills of this domain."),
    json_out: bool = _json_opt,
) -> None:
    """Skills you can use or install, with who owns each and whether it is installed.

    With no flags, every group. --mine, --org, --public and --builtin combine
    (``--mine --builtin`` lists both); the Local column says whether `beakr skills
    sync` keeps a Claude Code and a Codex copy.
    """
    for value in origin:
        if value not in _ORIGINS:
            raise _invalid("--origin", value, _ORIGINS)
    for value in visibility:
        if value not in _VISIBILITIES:
            raise _invalid("--visibility", value, _VISIBILITIES)
    owners = [
        owner
        for owner, wanted in (("me", mine), ("org", org), ("anyone", public), ("system", builtin))
        if wanted
    ]
    with get_sdk() as sdk:
        skills = sdk.skills.list(
            owner=owners or None,
            visibility=visibility or None,
            origin=origin or None,
            installed=installed,
            search=search,
            domain=domain,
        )
    rows = [{**skill, "local": local_status(str(skill.get("slug") or ""))} for skill in skills]

    def render(items: list[dict[str, Any]]) -> None:
        if not items:
            err_console.print("No skills match.")
            return
        # Local is the Claude Code / Codex copy state; the name and description
        # are in --json, which keeps the table inside a terminal's width.
        print_table(
            ["Slug", "Visibility", "Owner", "Origin", "Installed", "Local (Claude/Codex)"],
            [
                [
                    short(item.get("slug"), 32),
                    str(item.get("visibility") or ""),
                    str(item.get("owner") or ""),
                    str(item.get("origin") or ""),
                    _yes_no(item.get("installed")),
                    f"{item['local']['claude']}/{item['local']['codex']}",
                ]
                for item in items
            ],
        )

    emit(rows, json_out=json_out, render=render)


@app.command("show")
def show_skill(skill: str = _skill_arg, json_out: bool = _json_opt) -> None:
    """Print a skill's SKILL.md (--json: its labels, latest revision and every file)."""
    with get_sdk() as sdk:
        detail = sdk.skills.get(skill, include_content=True)
    if json_out:
        emit(detail, json_out=True)
        return
    text = str((detail.get("files") or {}).get(SKILL_MD, ""))
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


@app.command("download")
def download_skill(
    skill: str = _skill_arg,
    destination: Path = typer.Argument(
        Path("."), help="Directory to save into (default: here), or the folder or zip to write."
    ),
    as_zip: bool = typer.Option(False, "--zip", help="Save a zip of the folder instead."),
    json_out: bool = _json_opt,
) -> None:
    """Save a skill's SKILL.md folder (or a zip of it) to disk."""
    with get_sdk() as sdk:
        skill_id = str(sdk.skills.get(skill)["id"])
        saved = sdk.skills.download(skill_id, destination, as_zip=as_zip)
    emit(
        {"skill_id": skill_id, "path": str(saved)},
        json_out=json_out,
        render=lambda d: console.print(f"Saved {d['path']}", markup=False, highlight=False),
    )


@app.command("add")
def add_skill(
    source: Path = typer.Argument(
        ..., exists=True, help="A SKILL.md folder, a SKILL.md file, or a .zip of a folder."
    ),
    visibility: str = typer.Option(
        "private", "--visibility", help="Who may see it: private, org or public."
    ),
    origin_source: str | None = typer.Option(
        None, "--source", help="Where the folder came from, such as claude-code."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Create a skill of yours from a SKILL.md folder or zip; it is installed for you."""
    if visibility not in _VISIBILITIES:
        raise _invalid("--visibility", visibility, _VISIBILITIES)
    try:
        with get_sdk() as sdk:
            created = sdk.skills.create_from_folder(
                source, visibility=visibility, source=origin_source
            )
    except ValueError as exc:
        err_console.print(str(exc), markup=False, highlight=False)
        raise typer.Exit(2) from exc
    emit(
        created,
        json_out=json_out,
        render=lambda d: console.print(
            f"Added skill {d.get('slug')} ({d.get('id')}).", markup=False, highlight=False
        ),
    )


@app.command("install")
def install_skill(skill: str = _skill_arg, json_out: bool = _json_opt) -> None:
    """Add a skill to your library, so your agent is told about it."""
    with get_sdk() as sdk:
        found = sdk.skills.get(skill)
        result = sdk.skills.install(str(found["id"]))
    emit(
        result,
        json_out=json_out,
        render=lambda _: console.print(
            f"Installed {found.get('slug')}.", markup=False, highlight=False
        ),
    )


@app.command("uninstall")
def uninstall_skill(skill: str = _skill_arg, json_out: bool = _json_opt) -> None:
    """Remove a skill from your library; the skill itself is kept."""
    with get_sdk() as sdk:
        found = sdk.skills.get(skill)
        sdk.skills.uninstall(str(found["id"]))
    emit(
        {"skill_id": str(found["id"]), "slug": found.get("slug"), "installed": False},
        json_out=json_out,
        render=lambda d: console.print(f"Uninstalled {d['slug']}.", markup=False, highlight=False),
    )


@app.command("rm")
def delete_skill(
    skill: str = _skill_arg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    json_out: bool = _json_opt,
) -> None:
    """Delete a skill you wrote, for everyone who installed it. It cannot be restored."""
    with get_sdk() as sdk:
        found = sdk.skills.get(skill)
        if found.get("owner") != "me":
            err_console.print(
                f"'{found.get('slug')}' is not yours ({found.get('owner')}); "
                "`beakr skills uninstall` removes it from your library.",
                markup=False,
                highlight=False,
            )
            raise typer.Exit(2)
        confirm_or_require_yes(
            f"Delete skill {found.get('slug')} for everyone who installed it?", yes=yes
        )
        sdk.skills.delete(str(found["id"]))
    emit(
        {"skill_id": str(found["id"]), "slug": found.get("slug"), "deleted": True},
        json_out=json_out,
        render=lambda d: err_console.print(f"Deleted {d['slug']}.", markup=False),
        json_when_piped=False,
    )


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
        raise _invalid("--direction", direction, _DIRECTIONS)
    if target not in _TARGETS:
        raise _invalid("--target", target, _TARGETS)
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
    skill: str = _skill_arg,
    file: Path = typer.Option(
        ..., "--file", "-f", exists=True, dir_okay=False, help="Local file with the new content."
    ),
    path: str = typer.Option(
        SKILL_MD, "--path", help="File in the skill's bundle to replace (default SKILL.md)."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Replace one file of a skill you wrote (SKILL.md by default) with a local file.

    The rest of the skill's files are kept. A change made in Beakr since this
    command read the skill is refused rather than overwritten.
    """
    content = file.read_text(encoding="utf-8")
    with get_sdk() as sdk:
        found = sdk.skills.get(skill)
        if found.get("owner") != "me":
            err_console.print(
                f"'{found.get('slug')}' is not yours ({found.get('owner')}); only its "
                "author can change it.",
                markup=False,
                highlight=False,
            )
            raise typer.Exit(2)
        skill_id = str(found["id"])
        current = sdk.skills.bundle(skill_id)
        result = sdk.skills.upload_bundle(
            skill_id,
            {path: content},
            replace=False,
            expected_sha256=current.get("files_sha256"),
        )
    message = f"Updated {path} of {found.get('slug')}."
    emit(
        result,
        json_out=json_out,
        render=lambda _: console.print(message, highlight=False, markup=False),
    )


@app.command("proposals")
def list_proposals(
    status: str | None = typer.Option(None, "--status", help="pending, accepted or dismissed."),
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
        err_console.print(
            "Keep one with `beakr skills approve PROPOSAL_ID`, or `beakr skills dismiss`."
        )

    emit(proposals, json_out=json_out, render=render)


@app.command("approve")
def approve_proposal(
    proposal_id: str = typer.Argument(..., help="Proposal id from `beakr skills proposals`."),
    force: bool = typer.Option(
        False, "--force", help="Create it even when it repeats a skill you have."
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
            f"Installed skill from proposal {proposal_id}. Run `beakr skills sync` to pull it.",
            markup=False,
        ),
    )


@app.command("dismiss")
def dismiss_proposal(
    proposal_id: str = typer.Argument(..., help="Proposal id from `beakr skills proposals`."),
    json_out: bool = _json_opt,
) -> None:
    """Decline a proposed skill."""
    with get_sdk() as sdk:
        result = sdk.skills.proposals.dismiss(proposal_id)
    emit(
        result,
        json_out=json_out,
        render=lambda _: console.print(
            f"Dismissed proposal {proposal_id}.", markup=False, highlight=False
        ),
    )


def register(root: typer.Typer) -> None:
    """Attach ``beakr skills``."""
    root.add_typer(app, name="skills")
