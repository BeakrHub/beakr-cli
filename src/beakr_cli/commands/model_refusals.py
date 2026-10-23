"""``beakr projects refusals``: when a model refused a project's Knowledge work.

``GET /v1/projects/{project_id}/model-refusals/summary`` and ``.../files``
(through the SDK's ``projects.model_refusals``): for a window, how many times a
model refused a stage, how often the stage fell back to the next model, how the
work ended (recovered, failed, skipped), and which files it happened on.
"""

from __future__ import annotations

from typing import Any

import typer

from beakr_cli.commands.project_knowledge import _require_project
from beakr_cli.output import console, emit, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

_WINDOWS = ("24h", "7d", "30d")


def _summary_line(summary: dict[str, Any]) -> str:
    outcomes = summary.get("outcomes") or {}
    return (
        f"{summary.get('window') or ''}: {int(summary.get('refusals') or 0)} refusals, "
        f"{int(summary.get('fallbacks') or 0)} fallbacks, "
        f"{int(summary.get('files_affected') or 0)} files; "
        f"recovered={int(outcomes.get('recovered') or 0)} "
        f"failed={int(outcomes.get('failed') or 0)} "
        f"skipped={int(outcomes.get('skipped') or 0)}"
    )


def _file_line(row: dict[str, Any]) -> str:
    return (
        f"{row.get('artifact_id')}\t{row.get('title') or ''}"
        f"\trefusals={int(row.get('refusals') or 0)}"
        f"\tfallbacks={int(row.get('fallbacks') or 0)}\tfailed={int(row.get('failed') or 0)}"
    )


def refusals_command(
    project: str | None = typer.Argument(
        None,
        help="Project id, slug or name (default: the bound or configured project).",
        callback=project_ref_callback,
    ),
    window: str = typer.Option("7d", "--window", "-w", help="24h, 7d or 30d."),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=200, help="Files to list."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """How often models refused this project's Knowledge work, and on which files."""
    if window not in _WINDOWS:
        console.print(f"--window takes {', '.join(_WINDOWS)}.", markup=False)
        raise typer.Exit(2)
    project_id = _require_project(project)
    with get_sdk() as sdk:
        refusals = sdk.projects.model_refusals
        summary = refusals.summary(project_id, window)
        files = refusals.files(project_id, window=window, limit=limit)
    result = {"summary": summary, "files": (files or {}).get("data") or []}

    def render(r: dict[str, Any]) -> None:
        console.print(_summary_line(r["summary"]), markup=False, highlight=False)
        if not r["files"]:
            return
        print_table(
            ["ID", "File", "Refusals", "Fallbacks", "Failed", "Last"],
            [
                [
                    str(row.get("artifact_id") or ""),
                    short(row.get("title"), 40),
                    str(int(row.get("refusals") or 0)),
                    str(int(row.get("fallbacks") or 0)),
                    str(int(row.get("failed") or 0)),
                    str(row.get("last_event_at") or "")[:10],
                ]
                for row in r["files"]
            ],
            title="Files",
            whole=("ID",),
        )

    def lines(r: dict[str, Any]) -> list[str]:
        return [_summary_line(r["summary"]), *(_file_line(row) for row in r["files"])]

    emit(result, json_out=json_out, render=render, lines=lines)


def register(app: typer.Typer) -> None:
    app.command("refusals")(refusals_command)
