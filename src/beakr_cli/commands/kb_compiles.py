"""``beakr kb compiles ls|cancel``: the Knowledge compiles queued in a project, and skipping them.

Each file or connected item that comes in is compiled into Knowledge; compiles
wait in a queue per project. ``ls`` counts the queued and running compiles (in
the project, or for one source or file) and shows the progress of the last
cancel. ``cancel`` skips the queued ones: for the whole project, one source
(``--source``, an id from ``beakr sources ls``) or one file (``--file``).
Compiles already running are left to finish, and what Knowledge already wrote
stays. Cancelling a source's compiles also stops that source's running sync,
and cancelling the whole project's stops every running sync in the project.

Over ``GET /v1/projects/{project_id}/knowledge/compiles`` and ``POST
.../knowledge/compiles/cancel`` (:mod:`beakr_cli.cancels`).
"""

from __future__ import annotations

from typing import Any

import click
import typer

from beakr_cli import cancels
from beakr_cli.commands.kb import app as kb_app
from beakr_cli.commands.project_knowledge import _require_project
from beakr_cli.compact import cell
from beakr_cli.output import console, emit
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(
    help="Knowledge compiles queued in a project: count them, or cancel the queued ones.",
    no_args_is_help=True,
)

_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    "-s",
    help="Project id, slug or name (default: the bound or configured project).",
    callback=project_ref_callback,
)
_source_opt = typer.Option(
    None, "--source", help="Only this source's compiles (an id from `beakr sources ls`)."
)
_file_opt = typer.Option(None, "--file", help="Only this file's compiles (its file id).")
_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


def _target(source: str | None, file: str | None) -> tuple[str, str]:
    """``(scope, words)`` for what a command reaches: the project, a source or a file."""
    if source and file:
        raise click.UsageError("Give --source or --file, not both.")
    if source:
        return "source", f"for source {source}"
    if file:
        return "file", f"for file {file}"
    return "project", "in the project"


def _queue_line(data: dict[str, Any], scope: str, target: str | None) -> list[str]:
    fields = [
        scope,
        cell(target or "-"),
        f"queued={int(data.get('queued') or 0)}",
        f"running={int(data.get('running') or 0)}",
    ]
    last = data.get("last_cancel")
    if isinstance(last, dict) and last:
        fields += [
            f"last_cancel={cell(last.get('status') or '-')}",
            f"done={int(last.get('done') or 0)}/{int(last.get('total') or 0)}",
        ]
    return ["\t".join(fields)]


@app.command("ls")
def list_queue(
    source: str | None = _source_opt,
    file: str | None = _file_opt,
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """How many compiles are queued and running, and how the last cancel is going."""
    scope, words = _target(source, file)
    project_id = _require_project(project)
    with get_sdk() as sdk:
        answer = dict(
            cancels.compile_queue(sdk, project_id, attachment_id=source, artifact_id=file) or {}
        )
    queued = int(answer.get("queued") or 0)

    def render(data: dict[str, Any]) -> None:
        for line in cancels.queue_lines(data, words):
            console.print(line, highlight=False, markup=False, soft_wrap=True)

    flags = f" --source {source}" if source else f" --file {file}" if file else ""
    emit(
        answer,
        json_out=json_out,
        render=render,
        lines=lambda data: _queue_line(data, scope, source or file),
        hint=f"beakr kb compiles cancel{flags} -P {project_id}" if queued else None,
    )


@app.command("cancel")
def cancel(
    source: str | None = _source_opt,
    file: str | None = _file_opt,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Cancel the queued compiles of the project, or of one source or file.

    \b
    Compiles already running finish; pages Knowledge already wrote stay. With
    --source, that source's running sync is stopped too; without --source or
    --file, every running sync in the project is. A cancelled item is
    compiled again when its source next syncs (or the file is sent to
    Knowledge again). `beakr kb compiles ls` follows the cancel.
    A project admin may cancel the project's compiles; the person who attached
    a source or uploaded a file may cancel that one's.
    Exit codes: 0 cancelled (also when nothing was queued), 4 no such source or
    file, 3 not allowed.
    """
    scope, words = _target(source, file)
    project_id = _require_project(project)
    confirm_or_require_yes(
        f"Cancel the queued Knowledge compiles {words}"
        + (
            " and stop its running sync?"
            if source
            else "?"
            if file
            else " and stop every running sync in it?"
        ),
        yes=yes,
    )
    with get_sdk() as sdk:
        answer = dict(
            cancels.cancel_queued_compiles(sdk, project_id, attachment_id=source, artifact_id=file)
            or {}
        )

    def render(data: dict[str, Any]) -> None:
        for line in cancels.compile_cancel_lines(data, words):
            console.print(line, highlight=False, markup=False, soft_wrap=True)

    flags = f" --source {source}" if source else f" --file {file}" if file else ""
    emit(
        answer,
        json_out=json_out,
        render=render,
        lines=lambda data: [
            "\t".join(
                [
                    cell(data.get("cancel_id") or "-"),
                    str(data.get("status") or "cancelling"),
                    f"scope={data.get('scope') or scope}",
                    f"will_be_skipped={int(data.get('will_be_skipped') or 0)}",
                    f"already_running={int(data.get('already_running') or 0)}",
                    f"syncs_stopped={int(data.get('syncs_stopped') or 0)}",
                ]
            )
        ],
        hint=f"beakr kb compiles ls{flags} -P {project_id}",
    )


def register(root: typer.Typer) -> None:
    """Attach ``kb compiles`` (the root is unused)."""
    kb_app.add_typer(app, name="compiles")
