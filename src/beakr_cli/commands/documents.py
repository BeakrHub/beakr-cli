"""``beakr documents search``: the original documents' passages ranked by meaning.

The documents door (``POST /v1/knowledge/documents``) behind the SDK's
``knowledge.documents_search``: passages grouped by document, best first, each
document a source record whose ``source_ref`` ``beakr files download`` and
``read_file`` take. ``beakr kb grep`` searches Knowledge pages instead.
"""

from __future__ import annotations

import typer

from beakr_cli.output import console, emit
from beakr_cli.project_binding import project_is_pinned, resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Search the original documents' text by meaning.")


@app.command("search")
def search(
    query: str = typer.Argument(help="What the passages should be about, in your own words."),
    limit: int | None = typer.Option(
        None, "--limit", "-n", help="Documents per page (default 8, max 25)."
    ),
    offset: int | None = typer.Option(None, "--offset", help="Documents to skip (paging)."),
    passages: int | None = typer.Option(
        None, "--passages", help="Best passages per document (default 2, max 5)."
    ),
    path: str | None = typer.Option(
        None, "--path", help="A folder, a glob, or one document's path."
    ),
    glob: list[str] | None = typer.Option(
        None, "--glob", "-g", help="Drive-path glob; repeat for several, ! excludes."
    ),
    kind: list[str] | None = typer.Option(
        None, "--type", "-t", help="File kind (pdf, docx, xlsx, md, ...); repeat for several."
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help=(
            "Project id or name (default: this directory's bound project, "
            "else everything you can read)."
        ),
        callback=project_ref_callback,
    ),
    all_projects: bool = typer.Option(
        False, "--all", "-a", help="Search everything you can read, even in a bound directory."
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Find passages by meaning when you do not know the words; a ranked selection, not a census."""
    if project and all_projects:
        raise typer.BadParameter("Give --project or --all, not both.", param_hint="--all")
    # A pin (BEAKR_PROJECT_ID or this directory's binding) scopes the search the
    # way it scopes kb reads; the CLI's default project does not, so an unbound
    # directory still searches everything, as before.
    if project is None and not all_projects and project_is_pinned():
        project = resolve_project(None)
    with get_sdk() as sdk:
        data = sdk.knowledge.documents_search(
            query,
            limit=limit,
            offset=offset,
            passages=passages,
            path=path,
            glob=glob or None,
            type=kind or None,
            project_id=project,
        )
    # Piped, an agent reads the same ranked text (not a key=value summary of the
    # reply, which cut the passages to one column).
    emit(data, json_out=json_out, render=_render, lines=_lines)


def _lines(data: dict) -> list[str]:
    text = str(data.get("content") or "").rstrip("\n")
    return [*text.splitlines(), *(str(hint) for hint in data.get("next") or [])]


def _render(data: dict) -> None:
    console.print(data.get("content") or "", markup=False, highlight=False)
    for hint in data.get("next") or []:
        console.print(hint, markup=False, highlight=False)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="documents")
