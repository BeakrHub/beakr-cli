"""``beakr projects profile show|set|edit``: a project's Knowledge profile.

The profile tells Knowledge what a project is about (what it is for, the
people, organizations and topics that matter, the kinds of things to build
pages for, how to read its documents) and steers how its files are compiled.
``show`` reads it; ``set`` replaces it from a JSON or YAML file, or changes the
fields given as flags (creating the profile when the project has none); ``edit``
opens it as YAML in your editor and saves what you leave. Only a project's
owners may change it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import typer
import yaml
from beakr_sdk.errors import NotFoundError

from beakr_cli.commands.project_knowledge import _project_opt, _require_project
from beakr_cli.output import console, emit, err_console
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="A project's Knowledge profile: what Knowledge should understand about it.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")

#: The fields a profile write takes, in the order they are shown and edited.
FIELDS = (
    "name",
    "description",
    "industry",
    "goals",
    "focus_areas",
    "entity_types",
    "core_people",
    "core_organizations",
    "core_topics",
    "extraction_guidance",
)
_LISTS = {"goals", "focus_areas", "entity_types"}
_SAVED = "Saved the Knowledge profile; Knowledge reads it when it next compiles files."

_TEMPLATE_HELP = """\
# This project's Knowledge profile. Save to apply it; leave the file unchanged to
# cancel. Lists are YAML lists; people are {name, role, relevance}, organizations
# {name, type, relevance}, topics {name, relevance}. Only project owners may save.
"""


def _no_profile(project_id: str) -> None:
    err_console.print(
        f"Project {project_id} has no Knowledge profile yet. Set one with "
        "`beakr projects profile set` or `beakr projects profile edit`."
    )


def _is_missing(exc: NotFoundError) -> bool:
    return getattr(exc, "code", None) == "knowledge_profile_not_found"


def _read(sdk: Any, project_id: str) -> dict[str, Any] | None:
    try:
        return dict(sdk.projects.knowledge_profile(project_id))
    except NotFoundError as exc:
        if _is_missing(exc):
            return None
        raise


def _render(profile: dict[str, Any]) -> None:
    if profile.get("name"):
        console.print(f"[bold]{profile['name']}[/bold]", highlight=False, markup=True)
    for label, key in (("Description", "description"), ("Industry", "industry")):
        if profile.get(key):
            console.print(f"{label}: {profile[key]}", highlight=False, markup=False)
    for label, key in (
        ("Goals", "goals"),
        ("Focus areas", "focus_areas"),
        ("Entity types", "entity_types"),
    ):
        if profile.get(key):
            console.print(f"{label}: {', '.join(profile[key])}", highlight=False, markup=False)
    for label, key, extra in (
        ("People", "core_people", "role"),
        ("Organizations", "core_organizations", "type"),
        ("Topics", "core_topics", None),
    ):
        entries = profile.get(key) or []
        if entries:
            console.print(f"{label}:", highlight=False)
            for entry in entries:
                detail = f" ({entry[extra]})" if extra and entry.get(extra) else ""
                why = f": {entry['relevance']}" if entry.get("relevance") else ""
                console.print(
                    f"  - {entry.get('name')}{detail}{why}", highlight=False, markup=False
                )
    if profile.get("extraction_guidance"):
        console.print(
            f"Extraction guidance: {profile['extraction_guidance']}", highlight=False, markup=False
        )


def _lines(profile: dict[str, Any]) -> list[str]:
    out = []
    for key in FIELDS:
        value = profile.get(key)
        if value in (None, "", []):
            continue
        if key in _LISTS:
            out.append(f"{key}: {'; '.join(value)}")
        elif isinstance(value, list):
            out.append(f"{key}: " + "; ".join(str(entry.get("name")) for entry in value))
        else:
            out.append(f"{key}: {value}")
    return out or ["(empty profile)"]


def _editable(profile: dict[str, Any] | None) -> dict[str, Any]:
    """The fields a person edits, in order, with empty values for a new profile."""
    base: dict[str, Any] = {
        key: ([] if key in _LISTS or key.startswith("core_") else "") for key in FIELDS
    }
    for key in FIELDS:
        if profile and profile.get(key) not in (None,):
            base[key] = profile[key]
    return base


def _parse_document(text: str, source: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        raise typer.BadParameter(f"{source} is not valid JSON or YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise typer.BadParameter(f"{source} must hold one object of profile fields")
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise typer.BadParameter(
            f"{source} has unknown field(s): {', '.join(unknown)}; "
            f"the fields are {', '.join(FIELDS)}"
        )
    return {key: value for key, value in data.items() if value is not None}


def _entries(values: list[str], extra: str | None) -> list[dict[str, str]]:
    """``Name`` or ``Name:extra`` from flags into profile entries."""
    entries = []
    for value in values:
        name, _, detail = value.partition(":")
        entry = {"name": name.strip()}
        if extra and detail.strip():
            entry[extra] = detail.strip()
        entries.append(entry)
    return entries


def show_command(
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Show the project's Knowledge profile."""
    project_id = _require_project(project)
    with get_sdk() as sdk:
        profile = _read(sdk, project_id)
    if profile is None:
        _no_profile(project_id)
        raise typer.Exit(4)
    emit(profile, json_out=json_out, render=_render, lines=_lines)


def set_command(
    project: str | None = _project_opt,
    file: str | None = typer.Option(
        None,
        "--file",
        "-f",
        help="A JSON or YAML file holding the whole profile ('-' reads stdin); replaces it.",
    ),
    name: str | None = typer.Option(None, "--name", help="A short title for the profile."),
    description: str | None = typer.Option(
        None, "--description", "-d", help="What the project is about and what it is for."
    ),
    industry: str | None = typer.Option(None, "--industry", help="The field it works in."),
    goal: list[str] = typer.Option(  # noqa: B008
        [], "--goal", help="What Knowledge should help answer or track; repeatable."
    ),
    focus: list[str] = typer.Option(  # noqa: B008
        [], "--focus", help="An area to pay most attention to; repeatable."
    ),
    entity_type: list[str] = typer.Option(  # noqa: B008
        [],
        "--entity-type",
        "-t",
        help="A kind of thing to build a page for (batch, deviation, supplier); repeatable.",
    ),
    person: list[str] = typer.Option(  # noqa: B008
        [], "--person", help="A person to follow, as 'Name' or 'Name:Role'; repeatable."
    ),
    organization: list[str] = typer.Option(  # noqa: B008
        [],
        "--organization",
        help="An organization to follow, as 'Name' or 'Name:Type'; repeatable.",
    ),
    topic: list[str] = typer.Option(  # noqa: B008
        [], "--topic", help="A topic to follow; repeatable."
    ),
    guidance: str | None = typer.Option(
        None, "--guidance", help="How to read this project's documents."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Set the Knowledge profile: the whole profile from --file, or the fields given as flags.

    Flags change only the fields they name (a repeated flag replaces that list)
    and create the profile when the project has none. Only project owners may
    set it; Knowledge reads it when it next compiles files.
    """
    project_id = _require_project(project)
    fields: dict[str, Any] = {
        key: value
        for key, value in {
            "name": name,
            "description": description,
            "industry": industry,
            "extraction_guidance": guidance,
        }.items()
        if value is not None
    }
    for key, values in (("goals", goal), ("focus_areas", focus), ("entity_types", entity_type)):
        if values:
            fields[key] = list(values)
    for key, values, extra in (
        ("core_people", person, "role"),
        ("core_organizations", organization, "type"),
        ("core_topics", topic, None),
    ):
        if values:
            fields[key] = _entries(values, extra)
    if file is not None and fields:
        err_console.print("Pass --file or field flags, not both.")
        raise typer.Exit(2)
    if file is None and not fields:
        err_console.print("Nothing to set: pass --file, or at least one field flag.")
        raise typer.Exit(2)
    with get_sdk() as sdk:
        if file is not None:
            source = "stdin" if file == "-" else file
            text = sys.stdin.read() if file == "-" else Path(file).read_text(encoding="utf-8")
            result = sdk.projects.set_knowledge_profile(project_id, **_parse_document(text, source))
        else:
            try:
                result = sdk.projects.update_knowledge_profile(project_id, **fields)
            except NotFoundError as exc:
                if not _is_missing(exc):
                    raise
                result = sdk.projects.set_knowledge_profile(project_id, **fields)
    err_console.print(_SAVED)
    emit(result, json_out=json_out, render=_render, lines=_lines)


def edit_command(
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Edit the Knowledge profile as YAML in $EDITOR; saving the file applies it."""
    project_id = _require_project(project)
    with get_sdk() as sdk:
        current = _read(sdk, project_id)
        original = _TEMPLATE_HELP + yaml.safe_dump(
            _editable(current), sort_keys=False, allow_unicode=True, width=88
        )
        edited = click.edit(original, extension=".yaml")
        if edited is None or edited == original:
            err_console.print("No changes; the profile was left as it was.")
            return
        document = _parse_document(edited, "the edited profile")
        result = sdk.projects.set_knowledge_profile(project_id, **document)
    err_console.print(_SAVED)
    emit(result, json_out=json_out, render=_render, lines=_lines)


app.command("show")(show_command)
app.command("set")(set_command)
app.command("edit")(edit_command)


def register(projects_app: typer.Typer) -> None:
    """Attach ``beakr projects profile``."""
    projects_app.add_typer(app, name="profile")
