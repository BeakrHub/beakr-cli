"""File commands: ``beakr upload`` (also ``beakr files upload``) and
``beakr files ls|show|download|rm``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from beakr_sdk.resources._upload import iter_folder_files

from beakr_cli.commands.jobs import all_succeeded, wait_for_jobs
from beakr_cli.output import console, emit, err_console, page_rows, print_table, short
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="List, download and delete uploaded files.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    help="Project id, slug or name (default: the bound or configured project).",
    callback=project_ref_callback,
)


def upload_command(
    paths: list[Path] = typer.Argument(
        ...,
        help="Files or folders to upload. A folder's files keep their paths relative to "
        "it (docs/guides/a.md lands as guides/a.md); its own name is not added. Each file "
        "records where it sat (docs/guides/a.md, or deals/acme/memo.pdf as typed), which "
        "Knowledge reads name it by and read_file opens.",
    ),
    project: str | None = _project_opt,
    folder: str | None = typer.Option(None, "--folder", "-f", help="Folder id to file into."),
    no_mirror: bool = typer.Option(
        False,
        "--no-mirror",
        help="Put every file of a folder at the top level instead of recreating its "
        "subdirectories as folders.",
    ),
    no_knowledge: bool = typer.Option(
        False,
        "--no-knowledge",
        help="Store the files without compiling them into Knowledge.",
    ),
    resume: str | None = typer.Option(
        None,
        "--resume",
        metavar="UPLOAD_ID",
        help="Finish an interrupted large-file upload (one file path).",
    ),
    wait: bool = typer.Option(
        False,
        "--wait",
        "-w",
        help="Wait until every file is indexed (searchable); Knowledge pages compile "
        "afterwards, usually within a few minutes. Exit 1 if any indexing job failed.",
    ),
    timeout: float = typer.Option(
        900.0, "--timeout", min=1, help="With --wait: seconds to wait for each file."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Upload files or folders to a project. Indexing starts automatically.

    Each file's indexing runs as a background job; --wait blocks until they are
    done (or run `beakr jobs wait JOB_ID ...` later). Files over 64 MB are sent
    in resumable parts; if one is interrupted, the error names an upload id to
    pass to ``--resume``.
    """
    ingest = not no_knowledge
    if resume is not None:
        if len(paths) != 1 or not paths[0].is_file():
            err_console.print("--resume takes exactly one file: the one that was interrupted.")
            raise typer.Exit(2)
        with get_sdk(timeout=300.0) as sdk:
            err_console.print(f"Resuming upload {resume} of {paths[0]}...")
            resumed = sdk.files.resume_upload(resume, paths[0])
        emit(
            [{"path": str(paths[0]), **resumed}],
            json_out=json_out,
            render=lambda rows: console.print(
                f"Uploaded {rows[0]['path']} -> {(rows[0].get('artifact') or {}).get('id')}"
            ),
        )
        return
    project_id = resolve_project(project)
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"No such file or folder: {path}")
        if path.is_dir() and not project_id:
            err_console.print(
                f"Uploading a folder needs a project: pass --project or run `beakr init`. ({path})"
            )
            raise typer.Exit(2)

    results: list[dict[str, Any]] = []
    with get_sdk(timeout=300.0) as sdk:
        for path in paths:
            if path.is_dir():
                count = len(iter_folder_files(path))
                err_console.print(f"Uploading {path}/ ({count} files)...")
                uploaded = sdk.files.upload_folder(
                    path,
                    project_id=str(project_id),
                    folder_id=folder,
                    mirror_folders=not no_mirror,
                    ingest=ingest,
                )
                results.extend(uploaded)
            else:
                err_console.print(f"Uploading {path}...")
                uploaded_one = sdk.files.upload(
                    path, project_id=project_id, folder_id=folder, ingest=ingest
                )
                results.append({"path": str(path), **uploaded_one})

    job_ids = [str(row["job_id"]) for row in results if row.get("job_id")]
    if wait and job_ids:
        err_console.print(f"Waiting for {len(job_ids)} indexing job(s)...")
        with get_sdk(timeout=300.0) as sdk:
            states = dict(zip(job_ids, wait_for_jobs(sdk, job_ids, timeout=timeout)))
        for row in results:
            if row.get("job_id") in states:
                row["job"] = states[str(row["job_id"])]
    waited = wait and bool(job_ids)

    def render(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            artifact = row.get("artifact") or {}
            status = (row.get("job") or {}).get("status")
            console.print(
                f"Uploaded {row['path']} -> {artifact.get('id')}"
                + (f" ({status})" if status else "")
            )
        if waited:
            note = _KNOWLEDGE_NOTE.format(project=project_id or "<project>")
        elif ingest:
            note = "Indexing runs in the background."
        else:
            note = "Not sent to Knowledge (--no-knowledge)."
        console.print(f"[green]{len(rows)} file(s) uploaded.[/green] {note}")
        if ingest and job_ids and not waited:
            console.print(f"[dim]Wait for it: beakr jobs wait {' '.join(job_ids)}[/dim]")

    emit(
        results,
        json_out=json_out,
        render=render,
        lines=_upload_lines,
        hint=_upload_hint(
            job_ids,
            ingest=ingest,
            waited=waited,
            failed=waited and not all_succeeded(list(states.values())),
            project_id=project_id,
        ),
    )
    if waited and not all_succeeded(list(states.values())):
        raise typer.Exit(1)


#: What --wait waited for, and what still happens after it.
_KNOWLEDGE_NOTE = (
    "Indexed. Knowledge pages compile in the background, usually within a few "
    "minutes; `beakr kb ls -P {project}` to check."
)


def _upload_hint(
    job_ids: list[str], *, ingest: bool, waited: bool, failed: bool, project_id: str | None
) -> str | None:
    if not ingest or not job_ids or failed:
        return None
    if waited:
        return f"beakr kb ls -P {project_id or '<project>'}  (Knowledge compiles in a few minutes)"
    return f"beakr jobs wait {' '.join(job_ids)}"


def _upload_lines(rows: list[dict[str, Any]]) -> list[str]:
    """``file_id<TAB>path<TAB>job=JOB_ID[<TAB>status=...]`` per uploaded file."""
    lines = []
    for row in rows:
        artifact = row.get("artifact") or {}
        columns = [str(artifact.get("id") or "-"), str(row.get("path") or "")]
        if row.get("job_id"):
            columns.append(f"job={row['job_id']}")
        job = row.get("job") or {}
        if job.get("status"):
            columns.append(f"status={job['status']}")
        error = job.get("error")
        if isinstance(error, dict) and (error.get("message") or error.get("code")):
            columns.append(f"error={error.get('message') or error.get('code')}")
        lines.append("\t".join(columns))
    return lines


@app.command("ls")
def list_files(
    project: str | None = _project_opt,
    folder: str | None = typer.Option(None, "--folder", "-f", help="Folder id."),
    search: str | None = typer.Option(None, "--search", "-s", help="Title search."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max files (1-200)."),
    json_out: bool = _json_opt,
) -> None:
    """List uploaded files."""
    with get_sdk() as sdk:
        page = sdk.files.list(
            project_id=resolve_project(project),
            folder_id=folder,
            search=search,
            limit=max(1, min(limit, 200)),
        )
    items = page.get("data", []) if isinstance(page, dict) else page

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No files.")
            return
        print_table(
            ["ID", "Title", "Type", "Updated"],
            [
                [
                    str(a.get("id", "")),
                    short(a.get("title"), 48),
                    str(a.get("artifact_type") or ""),
                    str(a.get("updated_at") or "")[:10],
                ]
                for a in rows
            ],
            title="Files",
        )

    emit(items, json_out=json_out, render=render)


@app.command("show")
def show_file(
    artifact_id: str = typer.Argument(..., help="File id."),
    json_out: bool = _json_opt,
) -> None:
    """Show a file's details."""
    with get_sdk() as sdk:
        artifact = sdk.files.get(artifact_id)

    def render(a: dict[str, Any]) -> None:
        console.print(f"[bold]{a.get('title')}[/bold] ({a.get('id')})")
        for label, key in (
            ("Type", "artifact_type"),
            ("MIME", "mime_type"),
            ("Project", "home_project_id"),
            ("Version", "current_version_id"),
            ("Created", "created_at"),
            ("Updated", "updated_at"),
        ):
            if a.get(key):
                console.print(f"{label + ':':<9}{a[key]}")
        if a.get("description"):
            console.print(f"\n{a['description']}")

    emit(artifact, json_out=json_out, render=render)


@app.command("download")
def download_file(
    artifact_id: str = typer.Argument(..., help="File id."),
    out: Path = typer.Option(Path("."), "--out", "-o", help="Destination file or directory."),
    json_out: bool = _json_opt,
) -> None:
    """Download a file's original bytes."""
    with get_sdk(timeout=300.0) as sdk:
        saved = sdk.files.download(artifact_id, out)
    emit(
        {"id": artifact_id, "path": str(saved), "bytes": Path(saved).stat().st_size},
        json_out=json_out,
        render=lambda d: console.print(f"Saved {d['path']} ({d['bytes']} bytes)."),
    )


@app.command("rm")
def remove_file(
    artifact_id: str = typer.Argument(..., help="File id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    json_out: bool = _json_opt,
) -> None:
    """Delete a file."""
    confirm_or_require_yes(f"Delete file {artifact_id}?", yes)
    with get_sdk() as sdk:
        sdk.files.delete(artifact_id)
    emit(
        {"id": artifact_id, "deleted": True},
        json_out=json_out,
        render=lambda _: console.print(f"Deleted {artifact_id}."),
    )


@app.command("versions")
def file_versions(
    artifact_id: str = typer.Argument(..., help="File id."),
    json_out: bool = _json_opt,
) -> None:
    """A file's versions, newest first."""
    with get_sdk() as sdk:
        versions = sdk.files.versions(artifact_id)

    def render(page: Any) -> None:
        rows = page_rows(page)
        if not rows:
            err_console.print("No versions.")
            return
        ordered = sorted(rows, key=lambda v: v.get("version_no") or 0, reverse=True)
        print_table(
            ["Version", "Version ID", "Created", "Size", "MIME"],
            [
                [
                    str(v.get("version_no") or ""),
                    str(v.get("id") or ""),
                    str(v.get("created_at") or "")[:16],
                    f"{v['size_bytes']:,}" if isinstance(v.get("size_bytes"), int) else "",
                    str(v.get("mime_type") or ""),
                ]
                for v in ordered
            ],
            title=f"Versions of {artifact_id}",
        )

    emit(versions, json_out=json_out, render=render)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="files")
    root.command("upload")(upload_command)
    # The same command where people look for it beside `files ls`.
    app.command("upload")(upload_command)
