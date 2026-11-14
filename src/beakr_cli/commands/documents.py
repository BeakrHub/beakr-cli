"""``beakr documents``: search, grep, list and read the original documents' text.

The documents door (``POST /v1/knowledge/documents``), the ``documents`` tool
Beakr's own agents and the MCP servers use, one command each: ``search``
(passages ranked by meaning, grouped by document, each a source record whose
``source_ref`` ``beakr files download`` and ``read_file`` take), ``grep``
(every line matching a pattern), ``ls`` (the documents in scope) and ``read``
(a window of one document's lines). ``beakr kb grep`` searches Knowledge pages
instead.
"""

from __future__ import annotations

import json
from typing import Any

import click
import typer

from beakr_cli.errors import EXIT_ERROR
from beakr_cli.output import console, emit
from beakr_cli.project_binding import project_is_pinned, resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Search, grep, list and read the original documents' text.")

_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    help=(
        "Project id or name (default: this directory's bound project, "
        "else everything you can read)."
    ),
    callback=project_ref_callback,
)
_all_opt = typer.Option(
    False, "--all", "-a", help="Everything you can read, even in a bound directory."
)
_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_path_opt = typer.Option(None, "--path", help="A folder, a glob, or one document's path.")
_glob_opt = typer.Option(
    None, "--glob", "-g", help="Drive-path glob; repeat for several, ! excludes."
)
_after_document_opt = typer.Option(
    None,
    "--after-document",
    help="Continue after this document id (as a reply's notice names).",
)
_kind_opt = typer.Option(
    None, "--type", "-t", help="File kind (pdf, docx, xlsx, md, ...); repeat for several."
)


def _scoped_project(project: str | None, all_projects: bool) -> str | None:
    """The project a documents command runs in, or None for everything readable.

    A pin (BEAKR_PROJECT_ID or this directory's binding) scopes the command the
    way it scopes kb reads; the CLI's default project does not, so an unbound
    directory still covers everything.
    """
    if project and all_projects:
        raise typer.BadParameter("Give --project or --all, not both.", param_hint="--all")
    if project is None and not all_projects and project_is_pinned():
        return resolve_project(None)
    return project


def _run(command: str, *, project: str | None, json_out: bool, **arguments: Any) -> None:
    """Run one documents door command and print its reply; exit 1 when it is an error."""
    sent = {key: value for key, value in arguments.items() if value is not None}
    with get_sdk() as sdk:
        data = sdk.knowledge.documents(command, project_id=project, **sent)
    emit(data, json_out=json_out, render=_render, lines=_lines)
    if isinstance(data, dict) and data.get("is_error"):
        raise typer.Exit(EXIT_ERROR)


@app.command("read")
def read(
    document: str = typer.Argument(
        help="The document: its id, its path, or a citation's reference as a result prints it."
    ),
    start_line: int | None = typer.Option(
        None, "--start-line", min=1, help="First line of the window (default 1)."
    ),
    end_line: int | None = typer.Option(
        None,
        "--end-line",
        min=1,
        help="Last line of the window (default: 200 lines; at most 1,000 a read).",
    ),
    project: str | None = _project_opt,
    all_projects: bool = _all_opt,
    json_out: bool = _json_opt,
) -> None:
    """Read a window of one document's extracted text by line number.

    The lines a `documents grep` or `documents search` hit names open here. A
    window cut short says so and names the next --start-line.
    """
    _run(
        "read",
        project=_scoped_project(project, all_projects),
        json_out=json_out,
        document=document,
        start_line=start_line,
        end_line=end_line,
    )


@app.command("grep")
def grep(
    pattern: str = typer.Argument(help="A regex, as in ripgrep (literal text with -F)."),
    fixed_strings: bool = typer.Option(
        False, "--fixed-strings", "-F", help="Treat the pattern as literal text."
    ),
    ignore_case: bool = typer.Option(False, "--ignore-case", "-i", help="Ignore case."),
    context: int | None = typer.Option(
        None, "--context", "-C", min=0, help="Lines of context around each match (max 20)."
    ),
    output_mode: str | None = typer.Option(
        None,
        "--output-mode",
        click_type=click.Choice(["content", "files_with_matches", "count"]),
        help="content (default): matching lines. files_with_matches: documents. count: per file.",
    ),
    head_limit: int | None = typer.Option(
        None, "--limit", "-n", min=1, help="Lines or documents shown (default 50, max 500)."
    ),
    offset: int | None = typer.Option(
        None, "--offset", min=0, help="Results to skip (as a cut reply names)."
    ),
    path: str | None = _path_opt,
    glob: list[str] | None = _glob_opt,
    kind: list[str] | None = _kind_opt,
    document: str | None = typer.Option(
        None, "--document", help="Only this document (its id or path)."
    ),
    after_document: str | None = _after_document_opt,
    project: str | None = _project_opt,
    all_projects: bool = _all_opt,
    json_out: bool = _json_opt,
) -> None:
    """Find every line of the original documents that matches a pattern.

    For exact words, names and ids, and for counting; `documents search` ranks
    by meaning instead. A scan that stopped early says what it covered and how
    to continue.
    """
    _run(
        "grep",
        project=_scoped_project(project, all_projects),
        json_out=json_out,
        pattern=pattern,
        fixed_strings=True if fixed_strings else None,
        ignore_case=True if ignore_case else None,
        context=context,
        output_mode=output_mode,
        head_limit=head_limit,
        offset=offset,
        path=path,
        glob=glob or None,
        type=kind or None,
        document=document,
        after_document=after_document,
    )


@app.command("ls")
def ls(
    path: str | None = typer.Argument(None, help="A folder, a glob, or one document's path."),
    glob: list[str] | None = _glob_opt,
    kind: list[str] | None = _kind_opt,
    limit: int | None = typer.Option(
        None, "--limit", "-n", min=1, help="Documents per page (default 100, max 500)."
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Every document beneath the path, not one folder level."
    ),
    after_document: str | None = _after_document_opt,
    project: str | None = _project_opt,
    all_projects: bool = _all_opt,
    json_out: bool = _json_opt,
) -> None:
    """List the original documents in scope, with the id and path `documents read` takes."""
    _run(
        "ls",
        project=_scoped_project(project, all_projects),
        json_out=json_out,
        path=path,
        glob=glob or None,
        type=kind or None,
        limit=limit,
        recursive=True if recursive else None,
        after_document=after_document,
    )


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
    path: str | None = _path_opt,
    glob: list[str] | None = _glob_opt,
    kind: list[str] | None = _kind_opt,
    project: str | None = _project_opt,
    all_projects: bool = _all_opt,
    json_out: bool = _json_opt,
) -> None:
    """Find passages by meaning when you do not know the words; a ranked selection, not a census."""
    project = _scoped_project(project, all_projects)
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


def _coverage_line(data: dict) -> list[str]:
    """``Coverage: {...}`` when the reply says it is partial, as the Knowledge reads print it.

    The same test as the shared renderer's (``mcp_render.render``): without the
    line a scan that stopped early reads as the whole answer.
    """
    coverage = data.get("coverage")
    if isinstance(coverage, dict) and (
        coverage.get("complete") is False
        or coverage.get("has_more")
        or coverage.get("truncated")
        or coverage.get("next_cursor") is not None
    ):
        return ["Coverage: " + json.dumps(coverage, ensure_ascii=False, default=str)]
    return []


def _lines(data: dict) -> list[str]:
    text = str(data.get("content") or "").rstrip("\n")
    return [
        *text.splitlines(),
        *_coverage_line(data),
        *(str(hint) for hint in data.get("next") or []),
    ]


def _render(data: dict) -> None:
    console.print(data.get("content") or "", markup=False, highlight=False)
    for line in _coverage_line(data):
        # Not wrapped: one line of JSON, as the Knowledge reads print it.
        console.print(line, markup=False, highlight=False, soft_wrap=True)
    for hint in data.get("next") or []:
        console.print(hint, markup=False, highlight=False)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="documents")
