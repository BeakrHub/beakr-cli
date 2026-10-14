"""``beakr workflows ls|create|draft|run|schedule|runs``: repeatable agent work.

A workflow is a template (the steps), the inputs it runs with, the skills it
loads, where its outputs go, and optionally a schedule. ``create`` saves one;
``run`` starts it now; ``schedule`` sets its cron; ``runs`` shows what happened.
``draft`` asks Beakr to write a template definition from a description.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Workflows: saved, scheduled agent work and its runs.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_TERMINAL = {"completed", "failed", "cancelled", "error"}


def _inputs(pairs: list[str]) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"Expected KEY=VALUE, got {pair!r}", param_hint="--input")
        inputs[key.strip()] = value
    return inputs


@app.command("ls")
def list_workflows(
    templates: bool = typer.Option(
        False, "--templates", help="List the templates you can build a workflow from."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Your saved workflows (or, with --templates, the templates available)."""
    with get_sdk() as sdk:
        rows = sdk.workflows.templates() if templates else sdk.workflows.configs(is_saved=True)

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            err_console.print(
                "No templates."
                if templates
                else "No saved workflows. Create one with "
                "`beakr workflows create TEMPLATE_ID --name ...`."
            )
            return
        if templates:
            print_table(
                ["Template ID", "Name", "Slug", "Category"],
                [
                    [
                        str(t.get("id")),
                        short(t.get("name"), 40),
                        str(t.get("slug") or ""),
                        str(t.get("category") or ""),
                    ]
                    for t in data
                ],
                title=f"Templates ({len(data)})",
            )
            return
        print_table(
            ["Workflow ID", "Name", "Schedule", "Last run", "Runs"],
            [
                [
                    str(c.get("id")),
                    short(c.get("name"), 36),
                    str(c.get("schedule_cron") or "") if c.get("schedule_enabled") else "",
                    str(c.get("last_run_status") or ""),
                    str(c.get("run_count") or 0),
                ]
                for c in data
            ],
            title=f"Workflows ({len(data)})",
        )

    emit(rows, json_out=json_out, render=render)


@app.command("create")
def create_workflow(
    template_id: str = typer.Argument(
        ..., help="Template id from `beakr workflows ls --templates`."
    ),
    name: str = typer.Option(..., "--name", help="A name for the saved workflow."),
    input_: list[str] = typer.Option(  # noqa: B008
        [], "--input", "-i", help="Template input KEY=VALUE; repeatable."
    ),
    skill: list[str] = typer.Option(  # noqa: B008
        [], "--skill", help="Skill id the run loads; repeatable."
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="File outputs in this project.",
        callback=project_ref_callback,
    ),
    cron: str | None = typer.Option(None, "--cron", help="Run on this cron schedule."),
    timezone: str | None = typer.Option(None, "--tz", help="Timezone for --cron."),
    json_out: bool = _json_opt,
) -> None:
    """Save a workflow: a template plus inputs, skills, output location and schedule."""
    with get_sdk() as sdk:
        if cron:
            preview = sdk.workflows.preview_schedule(cron, timezone=timezone, count=3)
        created = sdk.workflows.create_config(
            template_id,
            name=name,
            inputs=_inputs(input_),
            skill_ids=list(skill) or None,
            output_location_type="project" if project else None,
            output_location_id=project,
            schedule_cron=cron,
            schedule_timezone=timezone,
            schedule_enabled=True if cron else None,
        )

    def render(data: dict[str, Any]) -> None:
        console.print(f"Saved workflow {data.get('name')} ({data.get('id')}).", highlight=False)
        if cron:
            upcoming = (preview or {}).get("upcoming_runs") or []
            when = ", ".join(str(t) for t in upcoming[:3])
            readable = (preview or {}).get("human_readable") or cron
            console.print(f"Scheduled: {readable}" + (f" (next: {when})" if when else ""))
        err_console.print(f"Run it now with `beakr workflows run {data.get('id')}`.")

    emit(created, json_out=json_out, render=render)


@app.command("draft")
def draft_workflow(
    description: str = typer.Argument(..., help="What the workflow should do, in words."),
    refine: Path | None = typer.Option(  # noqa: B008
        None,
        "--refine",
        help="A template definition (JSON file) to revise instead; DESCRIPTION is the change.",
        exists=True,
        dir_okay=False,
    ),
    out: Path | None = typer.Option(  # noqa: B008
        None, "--out", "-o", help="Write the drafted template definition to this JSON file."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Beakr drafts a workflow template definition; nothing is saved.

    With --refine, Beakr revises the template in that file as DESCRIPTION
    says. Save a draft as a template with the SDK's ``workflows.create_template``
    (``POST /v1/workflows/templates``).
    """
    with get_sdk() as sdk:
        if refine is not None:
            current = json.loads(refine.read_text())
            drafted = sdk.workflows.refine_template(current, description)
        else:
            drafted = sdk.workflows.generate_template(description)
    if out is not None:
        out.write_text(json.dumps(drafted.get("template") or {}, indent=2) + "\n")

    def render(data: dict[str, Any]) -> None:
        template = data.get("template") or {}
        name = template.get("name") or "(unnamed)"
        console.print(f"Drafted template: {name}", highlight=False)
        for change in data.get("changes_made") or []:
            console.print(f"  changed: {change}", highlight=False)
        for suggestion in data.get("suggestions") or []:
            console.print(f"  suggestion: {suggestion}", highlight=False)
        if out is not None:
            err_console.print(f"Wrote the definition to {out}.")
        else:
            err_console.print("Pass --out FILE to keep the definition, or --json for all of it.")

    emit(drafted, json_out=json_out, render=render)


@app.command("run")
def run_workflow(
    workflow_id: str = typer.Argument(..., help="Saved workflow id."),
    input_: list[str] = typer.Option(  # noqa: B008
        [], "--input", "-i", help="Override an input KEY=VALUE for this run; repeatable."
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait until the run finishes."),
    timeout: float = typer.Option(1800.0, "--timeout", help="Seconds to wait with --wait."),
    json_out: bool = _json_opt,
) -> None:
    """Start a saved workflow now."""
    with get_sdk() as sdk:
        run = sdk.workflows.run_config(workflow_id, input_overrides=_inputs(input_) or None)
        if wait and str(run.get("status")) not in _TERMINAL:
            from beakr_sdk.resources.jobs import JobTimeout

            # The run is a job (GET /v1/jobs/{id}); wait for it with backoff,
            # then read the run itself for its outputs.
            try:
                sdk.wait(str(run.get("job_id") or run["id"]), timeout=timeout)
            except JobTimeout as exc:
                err_console.print(
                    f"[yellow]Still {exc.last.get('native_status') or exc.last.get('status')} "
                    f"after {timeout:.0f}s.[/]"
                )
                raise typer.Exit(1) from None
            run = sdk.workflows.get_run(str(run["id"]))

    def render(data: dict[str, Any]) -> None:
        console.print(f"Run {data.get('id')}: {data.get('status')}", highlight=False)
        if data.get("output_canvas_id"):
            console.print(f"Output: {data['output_canvas_id']} (`beakr outputs show ...`)")
        if data.get("error_message"):
            err_console.print(f"[red]{data['error_message']}[/red]")

    emit(run, json_out=json_out, render=render)
    if wait and str(run.get("status")) != "completed":
        raise typer.Exit(1)


@app.command("schedule")
def schedule_workflow(
    workflow_id: str = typer.Argument(..., help="Saved workflow id."),
    cron: str | None = typer.Argument(None, help="Cron expression, e.g. '0 9 * * MON'."),
    timezone: str | None = typer.Option(None, "--tz", help="Timezone, e.g. America/New_York."),
    off: bool = typer.Option(False, "--off", help="Pause the schedule."),
    json_out: bool = _json_opt,
) -> None:
    """Run a saved workflow on a schedule (or pause it with --off)."""
    if not off and not cron:
        raise typer.BadParameter("Give a cron expression, or --off to pause.")
    with get_sdk() as sdk:
        if off:
            updated = sdk.workflows.update_config(workflow_id, schedule_enabled=False)
        else:
            preview = sdk.workflows.preview_schedule(cron, timezone=timezone, count=3)
            updated = sdk.workflows.schedule(workflow_id, cron, timezone=timezone)

    def render(data: dict[str, Any]) -> None:
        if off:
            console.print(f"Paused the schedule of {workflow_id}.")
            return
        upcoming = (preview or {}).get("upcoming_runs") or []
        when = ", ".join(str(t) for t in upcoming[:3])
        readable = (preview or {}).get("human_readable") or cron
        console.print(f"{workflow_id} runs {readable}" + (f" (next: {when})" if when else ""))

    emit(updated, json_out=json_out, render=render)


@app.command("runs")
def list_runs(
    workflow_id: str | None = typer.Argument(None, help="Only this workflow's runs."),
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200),
    json_out: bool = _json_opt,
) -> None:
    """Recent workflow runs."""
    with get_sdk() as sdk:
        if workflow_id:
            runs = sdk.workflows.config_runs(workflow_id, status=status, limit=limit)
        else:
            runs = sdk.workflows.runs(status=status, limit=limit)

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            err_console.print("No runs.")
            return
        print_table(
            ["Run ID", "Workflow", "Status", "Started", "Output"],
            [
                [
                    str(r.get("id")),
                    short(r.get("config_name") or r.get("template_name"), 30),
                    str(r.get("status")),
                    str(r.get("started_at") or r.get("created_at") or "")[:16],
                    str(r.get("output_canvas_id") or ""),
                ]
                for r in data
            ],
            title=f"Runs ({len(data)})",
        )

    emit(runs, json_out=json_out, render=render)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="workflows")
