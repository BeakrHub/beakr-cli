"""``beakr projects status`` and ``beakr projects retry``: a project's Knowledge.

Uploaded files reach a project's Knowledge in the background after indexing.
``status`` says whether it is ready (nothing queued or running), how far along
it is, and which files failed and why; ``retry`` sends failed files to
Knowledge again. ``beakr upload --wait-knowledge`` waits with the same reads
(:func:`wait_for_knowledge`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer
from beakr_sdk.resources.knowledge_readiness import KnowledgeTimeout

from beakr_cli.output import console, emit, err_console
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    help="Project id, slug or name (default: the bound or configured project).",
    callback=project_ref_callback,
)


def _require_project(project: str | None) -> str:
    project_id = resolve_project(project)
    if not project_id:
        err_console.print("Pass --project or run `beakr init` to bind this directory to one.")
        raise typer.Exit(2)
    return str(project_id)


def state_phrase(status: dict[str, Any]) -> str:
    """``ready``, ``ready, 2 failed``, ``queued (4 files)`` or ``compiling (3 running, ...)``."""
    queued, running = int(status.get("queued") or 0), int(status.get("running") or 0)
    failed = int(status.get("failed") or 0)
    if running:
        return f"compiling ({running} running, {queued} queued)"
    if queued:
        return f"queued ({queued} {'file' if queued == 1 else 'files'})"
    return f"ready, {failed} failed" if failed else "ready"


def progress_line(status: dict[str, Any]) -> str:
    """One stderr line per change while waiting: the counts that are moving."""
    return (
        f"Knowledge: {int(status.get('queued') or 0)} queued, "
        f"{int(status.get('running') or 0)} running, {int(status.get('failed') or 0)} failed"
    )


def failed_file_line(failed: dict[str, Any]) -> str:
    return (
        f"{failed.get('path') or failed.get('filename')}  {failed.get('error_code')}  "
        f"{failed.get('reason')}  ({failed.get('artifact_id')})"
    )


def _render_status(project_ref: str) -> Callable[[dict[str, Any]], None]:
    def render(status: dict[str, Any]) -> None:
        console.print(f"Knowledge: [bold]{state_phrase(status)}[/bold]")
        failed = int(status.get("failed") or 0)
        behind = max(0, int(status.get("eligible") or 0) - failed)
        skipped = int(status.get("knowledge_skipped") or 0)
        processed = f"{int(status.get('synced') or 0)} processed"
        if skipped:
            processed += f" ({skipped} with nothing to add)"
        parts = [f"{int(status.get('total') or 0)} files", processed]
        if behind:
            parts.append(f"{behind} not sent or out of date")
        console.print("Files: " + ", ".join(parts), highlight=False)
        shown = status.get("failed_files") or []
        if shown:
            console.print(f"Failed ({failed}):")
            for row in shown:
                console.print(f"  {failed_file_line(row)}", highlight=False, markup=False)
            if failed > len(shown):
                console.print(f"  ... and {failed - len(shown)} more (raise --limit to list them)")
            console.print(
                f"[dim]Retry them: beakr projects retry -P {project_ref}[/dim]", highlight=False
            )
        elif not status.get("ready"):
            console.print("[dim]Answers from this project may be incomplete until then.[/dim]")

    return render


def _status_lines(status: dict[str, Any]) -> list[str]:
    """``state<TAB>queued=..<TAB>running=..<TAB>failed=..`` then one line per failed file."""
    lines = [
        "\t".join(
            [
                "ready" if status.get("ready") else "building",
                *(f"{key}={int(status.get(key) or 0)}" for key in ("queued", "running", "failed")),
            ]
        )
    ]
    for row in status.get("failed_files") or []:
        lines.append(
            "\t".join(
                [
                    "failed",
                    str(row.get("artifact_id")),
                    str(row.get("path") or row.get("filename")),
                    str(row.get("error_code")),
                ]
            )
        )
    return lines


def status_command(
    project: str | None = _project_opt,
    limit: int = typer.Option(
        20, "--limit", min=0, max=200, help="Failed files to list (newest first)."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Whether the project's Knowledge is ready, and which files failed and why."""
    project_id = _require_project(project)
    with get_sdk() as sdk:
        status = sdk.projects.knowledge_status(project_id, failed_limit=limit)
    emit(
        status,
        json_out=json_out,
        render=_render_status(project or project_id),
        lines=_status_lines,
        hint=(f"beakr projects retry -P {project or project_id}" if status.get("failed") else None),
    )


def _render_retry(result: dict[str, Any]) -> None:
    queued = int(result.get("queued") or 0)
    if not result.get("files"):
        console.print("No failed files to retry.")
        return
    console.print(f"Sent {queued} {'file' if queued == 1 else 'files'} to Knowledge again.")
    for row in result.get("files") or []:
        if row.get("outcome") == "queued":
            continue
        reason = f": {row['reason']}" if row.get("reason") else ""
        line = f"  {row.get('artifact_id')}  {row.get('outcome')}{reason}"
        console.print(line, highlight=False, markup=False)
    if result.get("has_more"):
        console.print("More failed files remain; run this again once these finish.")


def _retry_lines(result: dict[str, Any]) -> list[str]:
    """``artifact_id<TAB>outcome`` per file the retry considered."""
    return [f"{row.get('artifact_id')}\t{row.get('outcome')}" for row in result.get("files") or []]


def retry_command(
    project: str | None = _project_opt,
    file: list[str] = typer.Option(
        None,
        "--file",
        help="Retry only this file id (repeatable; ids from `beakr projects status`).",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Send the project's failed files to Knowledge again (all of them, or --file ones).

    Safe to repeat: files already queued or running are left alone. Exit 1 when a
    named file could not be retried.
    """
    project_id = _require_project(project)
    with get_sdk() as sdk:
        result = sdk.projects.retry_failed(project_id, list(file) if file else None)
    emit(
        result,
        json_out=json_out,
        render=_render_retry,
        lines=_retry_lines,
        hint=f"beakr projects status -P {project or project_id}" if result.get("queued") else None,
    )
    if result.get("skipped") or (file and result.get("not_found")):
        raise typer.Exit(1)


def wait_for_knowledge(sdk: Any, project_id: str, *, timeout: float) -> dict[str, Any] | None:
    """Wait until the project's Knowledge is ready, printing a line each time the counts move.

    Returns the final status, or ``None`` after printing why when ``timeout``
    passed first.
    """
    printed: list[str] = []

    def show(status: dict[str, Any]) -> None:
        line = progress_line(status)
        if not printed or printed[-1] != line:
            err_console.print(line, highlight=False)
            printed.append(line)

    try:
        return sdk.projects.wait_until_ready(project_id, timeout=timeout, poll=5.0, on_status=show)
    except KnowledgeTimeout as exc:
        err_console.print(
            f"Knowledge is still {state_phrase(exc.last)} after {timeout:.0f}s; "
            f"check later with `beakr projects status -P {project_id}`.",
            highlight=False,
        )
        return None


def failed_among(sdk: Any, project_id: str, status: dict[str, Any], ids: set[str]) -> list[dict]:
    """The failed files of ``status`` whose id is in ``ids``, reading every page if needed."""
    shown = status.get("failed_files") or []
    rows = (
        shown
        if int(status.get("failed") or 0) <= len(shown)
        else sdk.projects.knowledge_failures(project_id)
    )
    return [row for row in rows if str(row.get("artifact_id")) in ids]


def register(app: typer.Typer) -> None:
    app.command("status")(status_command)
    app.command("retry")(retry_command)
