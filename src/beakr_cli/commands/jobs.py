"""``beakr jobs show|wait``: background work started by another command.

An upload, an import, a sync or an agent run answers with a ``job_id``; these
read it (``GET /v1/jobs/{id}``) or block until it is final, so a script or an
agent can wait for indexing without polling by hand::

    beakr upload docs/ -P platform-docs      # prints each file's job id
    beakr jobs wait JOB_ID [JOB_ID ...]      # exits 1 if any failed
"""

from __future__ import annotations

from typing import Any

import typer
from beakr_sdk.resources.jobs import JobTimeout

from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Read background jobs (indexing, imports, syncs) or wait for them.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")

#: Job states that mean the work did not happen.
_UNSUCCESSFUL = frozenset({"failed", "cancelled"})


def _render_jobs(jobs: list[dict[str, Any]]) -> None:
    print_table(
        ["Job", "Kind", "Status", "Progress", "Error"],
        [
            [
                str(job.get("job_id") or ""),
                str(job.get("kind") or ""),
                str(job.get("status") or ""),
                _progress(job.get("progress")),
                short(_error(job), 50),
            ]
            for job in jobs
        ],
    )


def _progress(progress: Any) -> str:
    if isinstance(progress, dict) and progress.get("done") is not None:
        total = progress.get("total")
        return f"{progress['done']}/{total}" if total else str(progress["done"])
    return ""


def _error(job: dict[str, Any]) -> str:
    error = job.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")
    return str(error or "")


def wait_for_jobs(sdk: Any, job_ids: list[str], *, timeout: float) -> list[dict[str, Any]]:
    """Wait for every job, printing one stderr line as each finishes; return the final states.

    A job still running at ``timeout`` is returned with its last state (and a
    note on stderr) rather than raising, so the caller reports every job.
    """
    finished: list[dict[str, Any]] = []
    for index, job_id in enumerate(job_ids, 1):
        try:
            state = sdk.jobs.wait(job_id, timeout=timeout)
        except JobTimeout as exc:
            state = {**exc.last, "job_id": job_id}
            err_console.print(
                f"[{index}/{len(job_ids)}] {job_id} still {state.get('status')} "
                f"after {timeout:.0f}s",
                highlight=False,
            )
        else:
            err_console.print(
                f"[{index}/{len(job_ids)}] {job_id} {state.get('status')}"
                + (f": {_error(state)}" if _error(state) else ""),
                highlight=False,
            )
        finished.append(state)
    return finished


def all_succeeded(jobs: list[dict[str, Any]]) -> bool:
    return all(job.get("status") == "succeeded" for job in jobs)


def duplicate_of(job: dict[str, Any]) -> dict[str, Any] | None:
    """The kept file when indexing rejected this upload as a duplicate, else None.

    Indexing does not store bytes already in the same place twice: it fails the
    new file's job with ``error.code`` ``duplicate`` and keeps the existing file,
    named in ``error.duplicate_of``. The content is in Beakr, so the upload is
    "already uploaded", not failed.
    """
    error = job.get("error")
    if job.get("status") != "failed" or not isinstance(error, dict):
        return None
    if error.get("code") != "duplicate":
        return None
    kept = error.get("duplicate_of")
    if not isinstance(kept, dict) or not kept.get("artifact_id"):
        return None
    return kept


def all_uploaded(jobs: list[dict[str, Any]]) -> bool:
    """Every upload's job succeeded, or found its bytes already uploaded."""
    return all(job.get("status") == "succeeded" or duplicate_of(job) for job in jobs)


@app.command("show")
def show_jobs(
    job_ids: list[str] = typer.Argument(..., help="One or more job ids."),
    json_out: bool = _json_opt,
) -> None:
    """A job's kind, status, progress and error (one line per job)."""
    with get_sdk() as sdk:
        jobs = [sdk.jobs.get(job_id) for job_id in job_ids]
    pending = [str(j.get("job_id")) for j in jobs if j.get("status") in ("queued", "running")]
    emit(
        jobs,
        json_out=json_out,
        render=_render_jobs,
        hint=f"beakr jobs wait {' '.join(pending)}" if pending else None,
    )


@app.command("wait")
def wait_jobs(
    job_ids: list[str] = typer.Argument(..., help="One or more job ids."),
    timeout: float = typer.Option(
        600.0, "--timeout", min=1, help="Seconds to wait for each job before giving up."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Wait until every job is final; exit 1 if any failed, was cancelled or timed out."""
    with get_sdk() as sdk:
        jobs = wait_for_jobs(sdk, job_ids, timeout=timeout)

    def render(rows: list[dict[str, Any]]) -> None:
        _render_jobs(rows)
        if all_succeeded(rows):
            console.print(f"[green]{len(rows)} job(s) succeeded.[/green]")

    emit(jobs, json_out=json_out, render=render)
    if not all_succeeded(jobs):
        raise typer.Exit(1)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="jobs")
