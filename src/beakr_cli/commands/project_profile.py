"""``beakr projects profile show|set|edit|setup``: a project's Knowledge profile.

The profile tells Knowledge what a project is about and steers how its files
are compiled. Its most important part is the entity types: the kinds of things
to build pages for (lots, deviations, compounds, assays). It also holds what the
project is for, the people, organizations and topics that matter, and how to
read its documents. ``show`` reads it; ``set`` replaces it from a JSON or YAML
file, or changes the fields given as flags (creating the profile when the
project has none); ``edit`` opens it as YAML in your editor and saves what you
leave; ``setup`` sets it up in a conversation with Beakr's agent, here in the
terminal. Only a project's owners may change it.

An organization can require a profile on every new project. Until the profile
has content, such a project takes no files, connector sources or Knowledge: the
API answers 409 ``project_profile_required`` and ``setup`` or ``set`` is the way
out.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import typer
import yaml
from beakr_sdk.errors import NotFoundError
from rich.markup import escape

from beakr_cli.commands.project_knowledge import _project_opt, _require_project
from beakr_cli.output import console, emit, err_console
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="A project's Knowledge profile: what Knowledge should understand about it.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")

#: The fields a profile write takes, in the order they are shown and edited.
FIELDS = (
    "name",
    "entity_types",
    "description",
    "industry",
    "goals",
    "focus_areas",
    "core_people",
    "core_organizations",
    "core_topics",
    "extraction_guidance",
)
_LISTS = {"goals", "focus_areas", "entity_types"}
_SAVED = "Saved the Knowledge profile; Knowledge reads it when it next compiles files."

_TEMPLATE_HELP = """\
# This project's Knowledge profile. Save to apply it; leave the file unchanged to
# cancel. entity_types come first: the kinds of things Knowledge builds pages for
# (lot, deviation, compound), singular snake_case. Lists are YAML lists; people
# are {name, role, relevance}, organizations {name, type, relevance}, topics
# {name, relevance}. Only project owners may save.
"""


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
        console.print(f"[bold]{escape(str(profile['name']))}[/bold]", highlight=False)
    # First: the kinds of things Knowledge builds pages for.
    if profile.get("entity_types"):
        console.print(
            f"Entity types: {', '.join(profile['entity_types'])}", highlight=False, markup=False
        )
    for label, key in (("Description", "description"), ("Industry", "industry")):
        if profile.get(key):
            console.print(f"{label}: {profile[key]}", highlight=False, markup=False)
    for label, key in (
        ("Goals", "goals"),
        ("Focus areas", "focus_areas"),
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
            console.print(f"{label}:", highlight=False, markup=False)
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
        # Raised, not printed: the root group exits 4 and, under --json,
        # prints the error as JSON.
        raise NotFoundError(
            f"Project {project_id} has no Knowledge profile yet. Set one with "
            "`beakr projects profile set` or `beakr projects profile edit`.",
            code="knowledge_profile_not_found",
            status=404,
        )
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
    err_console.print(_SAVED, markup=False)
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
    err_console.print(_SAVED, markup=False)
    emit(result, json_out=json_out, render=_render, lines=_lines)


#: What starts a setup conversation when the person does not.
_OPENING = "Help me set up this project's Knowledge profile, starting with its entity types."
#: Lines that end the conversation.
_DONE = {"", "/done", "/quit", "/exit"}
_FAILED = {"error", "failed", "cancelled"}


def _setup_body(
    project_id: str, message: str, *, thread_id: str | None, answer: bool, required: bool
) -> dict[str, Any]:
    """The run ``projects.start_profile_setup`` (beakr-sdk) starts.

    Built here and sent with ``request`` so the CLI also works with a released
    SDK that predates that method.
    """
    import uuid

    body: dict[str, Any] = {
        "idempotency_key": uuid.uuid4().hex,
        "message": message,
        "home_project_id": project_id,
        "scope": {"knowledge_graph": True, "projects": [{"id": project_id}]},
        # A client with no profile panel, held to the profile.
        "profile_builder_meta": {
            "profile_scope": "project",
            "project_id": project_id,
            "required": required,
            "setup_only": True,
            "surface": "terminal",
        },
    }
    if thread_id:
        body["thread_id"] = thread_id
    if answer:
        body["is_ask_user_response"] = True
    return body


def _saved_summary(profile: dict[str, Any]) -> str:
    """One line for a ``profile_update`` event: the entity types, then what else is set."""
    parts = []
    if profile.get("entity_types"):
        parts.append(f"entity types: {', '.join(map(str, profile['entity_types']))}")
    for label, key in (
        ("description", "description"),
        ("industry", "industry"),
        ("goals", "goals"),
        ("focus areas", "focus_areas"),
        ("people", "core_people"),
        ("organizations", "core_organizations"),
        ("topics", "core_topics"),
        ("extraction guidance", "extraction_guidance"),
    ):
        value = profile.get(key)
        if isinstance(value, list) and value:
            parts.append(f"{label} ({len(value)})")
        elif isinstance(value, str) and value.strip():
            parts.append(label)
    return "; ".join(parts)


def _turn(sdk: Any, run_id: str) -> str:
    """Stream one reply of the agent to stdout; return how the run ended."""
    from beakr_cli.cited_answer import print_cited_sources
    from beakr_cli.commands.runs import _CitationStream

    order: list[str] = []
    sources: dict[str, dict[str, Any]] = {}
    citations = _CitationStream(order)
    status = "completed"
    wrote = ""
    for event in sdk.runs.stream(run_id):
        kind = event.get("type")
        data = event.get("data") or {}
        if kind == "content_delta" and not data.get("is_thinking"):
            chunk = citations.feed(str(data.get("delta") or ""))
            if chunk:
                wrote = chunk
                sys.stdout.write(chunk)
                sys.stdout.flush()
        elif kind == "profile_update":
            summary = _saved_summary(data)
            if summary:
                err_console.print(f"[green]Saved[/green] {escape(summary)}", soft_wrap=True)
        elif kind == "sources":
            for source in data.get("sources") or []:
                if source.get("key"):
                    sources[str(source["key"])] = source
        elif kind == "error":
            err_console.print(f"[red]{escape(str(data.get('message') or data))}[/red]")
        elif kind == "stream_complete":
            status = str(data.get("status") or status)
    rest = citations.finish()
    if rest:
        wrote = rest
        sys.stdout.write(rest)
    if wrote and not wrote.endswith("\n"):
        sys.stdout.write("\n")
    print_cited_sources(order, sources)
    return status


def _question(run: dict[str, Any]) -> dict[str, Any] | None:
    question = run.get("question")
    if run.get("status") == "interrupted" and isinstance(question, dict):
        return question
    return None


def setup_command(
    project: str | None = _project_opt,
    message: str | None = typer.Option(
        None, "--message", "-m", help="Your first message (default: ask the agent to begin)."
    ),
    thread: str | None = typer.Option(
        None, "--thread", help="Continue an earlier setup conversation."
    ),
    answer: bool = typer.Option(
        False, "--answer", help="--message answers the question the agent asked (with --thread)."
    ),
) -> None:
    """Set up the Knowledge profile with Beakr's agent, in a conversation here.

    The agent asks about the project, starting with its entity types (the kinds
    of things Knowledge keeps pages for, with examples, attributes and how they
    relate), then what it is for and who and what matters, and saves the profile
    as it goes. Type your answers; an empty line or /done ends the conversation.
    Without a terminal it runs one turn and exits: continue with --thread and
    --message (and --answer for the agent's question). Only project owners may
    save a profile.
    """
    from beakr_cli.output import is_interactive
    from beakr_cli.prompts import prompt_text

    project_id = _require_project(project)
    with get_sdk(timeout=300.0) as sdk:
        details = dict(sdk.projects.get(project_id))
        required = bool(details.get("requires_knowledge_profile"))
        name = str(details.get("name") or project_id)
        err_console.print(
            f"Setting up the Knowledge profile for [bold]{escape(name)}[/bold] with Beakr's "
            "agent, starting with its entity types: the kinds of things Knowledge keeps pages "
            "for. Answer its questions; an empty line or /done ends the conversation.",
            soft_wrap=True,
        )
        if required:
            err_console.print(
                "Your organization requires this profile: files and Knowledge cannot be "
                "added to the project until it has content.",
                soft_wrap=True,
            )
        text = message or _OPENING
        interactive = is_interactive()
        while True:
            run = sdk.request(
                "POST",
                "/v1/runs",
                json=_setup_body(
                    project_id, text, thread_id=thread, answer=answer, required=required
                ),
            )
            run_id = str(run.get("run_id") or run.get("id"))
            thread = str(run.get("thread_id") or thread)
            status = _turn(sdk, run_id)
            if status in _FAILED:
                err_console.print(f"[red]The agent's run ended {escape(status)}.[/red]")
                raise typer.Exit(1)
            question = _question(dict(sdk.runs.get(run_id)))
            options = [str(option) for option in (question or {}).get("options") or []]
            if question:
                console.print(str(question.get("text") or ""), markup=False, highlight=False)
                for number, option in enumerate(options, 1):
                    console.print(f"  {number}. {option}", markup=False, highlight=False)
            if not interactive:
                # One turn per call: a script continues the thread itself.
                flag = " --answer" if question else ""
                err_console.print(
                    f"Reply with `beakr projects profile setup -P {project_id} --thread "
                    f'{thread}{flag} -m "..."`.',
                    markup=False,
                    soft_wrap=True,
                )
                break
            line = prompt_text("> ", flag_hint="--thread and --message")
            if line.strip().lower() in _DONE:
                break
            choice = line.strip()
            if options and choice.isdigit() and 1 <= int(choice) <= len(options):
                choice = options[int(choice) - 1]
            text, answer = choice, question is not None
        current = _read(sdk, project_id)
        still_required = bool(dict(sdk.projects.get(project_id)).get("requires_knowledge_profile"))
    console.print()
    if current:
        _render(current)
    else:
        err_console.print("No profile was saved yet.")
    if still_required:
        err_console.print(
            "[yellow]The project still needs a profile with content before files or "
            "Knowledge can be added.[/yellow]",
            soft_wrap=True,
        )
    if thread:
        err_console.print(
            f"Continue with `beakr projects profile setup -P {project_id} --thread {thread}`.",
            markup=False,
            soft_wrap=True,
        )


app.command("show")(show_command)
app.command("set")(set_command)
app.command("edit")(edit_command)
app.command("setup")(setup_command)


def register(projects_app: typer.Typer) -> None:
    """Attach ``beakr projects profile``."""
    projects_app.add_typer(app, name="profile")
