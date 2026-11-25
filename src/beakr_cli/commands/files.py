"""File commands: ``beakr upload`` (also ``beakr files upload``) and
``beakr files ls|show|approve|download|rm``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import click
import typer
from beakr_sdk.errors import BeakrError, PermissionDeniedError
from beakr_sdk.resources._upload import iter_folder_files
from rich.markup import escape

from beakr_cli._private_api.resources.files import refused_row
from beakr_cli.commands.jobs import all_uploaded, duplicate_of, wait_for_jobs
from beakr_cli.compact import cell, summary_line
from beakr_cli.output import console, emit, err_console, page_rows, print_table, short
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk
from beakr_cli.upload_placement import refused_placement

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


_ON_EXISTING = ("version", "new")


def _on_existing_choice(value: str | None) -> str | None:
    if value is None:
        return None
    choice = value.strip().lower()
    if choice not in _ON_EXISTING:
        raise typer.BadParameter("must be 'version' or 'new'", param_hint="--on-existing")
    return choice


def upload_command(
    paths: list[Path] = typer.Argument(
        ...,
        help="Files or folders to upload. A folder's own name becomes the top folder, "
        "with its subdirectories as folders inside it (uploading docs/ puts "
        "docs/guides/a.md in the folder docs/guides); --no-mirror puts every file at the "
        "top level instead. Each file records where it sat (docs/guides/a.md, or "
        "deals/acme/memo.pdf as typed), which Knowledge reads name it by and read_file "
        "opens.",
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
    on_existing: str | None = typer.Option(
        None,
        "--on-existing",
        metavar="version|new",
        help="When the folder already holds a file with the same name: 'version' (the "
        "server's default) adds this upload to it as a new version and updates the "
        "Knowledge built from it; 'new' always creates a separate file.",
        callback=_on_existing_choice,
    ),
    version_of: str | None = typer.Option(
        None,
        "--version-of",
        metavar="FILE_ID",
        help="Add the upload as a new version of this file (same project), whatever the "
        "upload is called. One file only.",
    ),
    written_by_agent: bool = typer.Option(
        False,
        "--written-by-agent",
        help="Mark the upload as an agent's work, not a person's: the version records it, "
        "and a file it creates is marked as written by Beakr.",
    ),
    author: list[str] | None = typer.Option(
        None,
        "--author",
        metavar="NAME",
        help="Who wrote the file, if not you: a name, 'Name <email>' or an email; repeat "
        "it for co-authors (up to 20). Knowledge attributes what it states to them.",
    ),
    date: str | None = typer.Option(
        None,
        "--date",
        metavar="DATE",
        help="When it was written: YYYY-MM-DD or an ISO 8601 date-time (not in the "
        "future). Knowledge dates its rows by it when the text states no date.",
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
    wait_knowledge: bool = typer.Option(
        False,
        "--wait-knowledge",
        help="Wait until every file is indexed and the project's Knowledge is ready "
        "(nothing queued or running), printing progress. Exit 1 if any file failed to "
        "index or to reach Knowledge; `beakr projects retry` sends failed files again.",
    ),
    timeout: float = typer.Option(
        900.0,
        "--timeout",
        min=1,
        help="With --wait: seconds to wait for each file; with --wait-knowledge, also "
        "for Knowledge.",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Upload files or folders to a project. Indexing starts automatically.

    Each file's indexing runs as a background job; --wait blocks until they are
    done (or run `beakr jobs wait JOB_ID ...` later). Files over 64 MB are sent
    in resumable parts; if one is interrupted, the error names an upload id to
    pass to ``--resume``.

    Uploading to a folder that already holds a file with the same name adds a
    version to that file (--on-existing new makes a separate file instead).

    --author and --date record who wrote the files and when, apart from who
    uploads them (every file of the command gets them); `beakr files edit`
    changes them later.
    """
    ingest = not no_knowledge
    if wait_knowledge and not ingest:
        err_console.print("--wait-knowledge waits for Knowledge; --no-knowledge keeps it out.")
        raise typer.Exit(2)
    wait = wait or wait_knowledge
    if resume is not None:
        if len(paths) != 1 or not paths[0].is_file():
            err_console.print("--resume takes exactly one file: the one that was interrupted.")
            raise typer.Exit(2)
        placement = _placement_options(on_existing, written_by_agent, version_of, author, date)
        with get_sdk(timeout=300.0) as sdk:
            err_console.print(f"Resuming upload {resume} of {paths[0]}...", markup=False)
            try:
                row = {
                    "path": str(paths[0]),
                    **sdk.files.resume_upload(resume, paths[0], **placement),
                }
            except BeakrError as exc:
                if not refused_placement(exc):
                    raise
                row = refused_row(paths[0], exc)

        if row.get("refused"):
            _print_refused(row, project_id=None)

        def render_resumed(rows: list[dict[str, Any]]) -> None:
            if rows[0].get("refused"):
                return
            console.print(
                f"Uploaded {rows[0]['path']} -> {(rows[0].get('artifact') or {}).get('id')}"
                + _placement_note(rows[0]),
                markup=False,
            )

        emit([row], json_out=json_out, render=render_resumed, lines=_upload_lines)
        if row.get("refused"):
            raise typer.Exit(1)
        return
    if version_of is not None and (len(paths) != 1 or not paths[0].is_file()):
        err_console.print("--version-of takes exactly one file: the new version's bytes.")
        raise typer.Exit(2)
    project_id = resolve_project(project)
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"No such file or folder: {path}")
        if path.is_dir() and not project_id:
            err_console.print(
                f"Uploading a folder needs a project: pass --project or run `beakr init`. ({path})",
                markup=False,
            )
            raise typer.Exit(2)

    results: list[dict[str, Any]] = []
    placement = _placement_options(on_existing, written_by_agent, version_of, author, date)
    folder_placement = {key: value for key, value in placement.items() if key != "version_of"}
    with get_sdk(timeout=300.0) as sdk:
        for path in paths:
            if path.is_dir():
                count = len(iter_folder_files(path))
                err_console.print(f"Uploading {path}/ ({count} files)...", markup=False)
                uploaded = sdk.files.upload_folder(
                    path,
                    project_id=str(project_id),
                    folder_id=folder,
                    mirror_folders=not no_mirror,
                    ingest=ingest,
                    **folder_placement,
                )
                results.extend(uploaded)
            else:
                err_console.print(f"Uploading {path}...", markup=False)
                try:
                    uploaded_one = sdk.files.upload(
                        path, project_id=project_id, folder_id=folder, ingest=ingest, **placement
                    )
                except BeakrError as exc:
                    if not refused_placement(exc):
                        raise
                    results.append(refused_row(path, exc))
                    continue
                results.append({"path": str(path), **uploaded_one})
    refused = [row for row in results if row.get("refused")]

    job_ids = [str(row["job_id"]) for row in results if row.get("job_id")]
    if wait and job_ids:
        err_console.print(f"Waiting for {len(job_ids)} indexing job(s)...")
        with get_sdk(timeout=300.0) as sdk:
            states = dict(zip(job_ids, wait_for_jobs(sdk, job_ids, timeout=timeout)))
        for row in results:
            if row.get("job_id") in states:
                row["job"] = states[str(row["job_id"])]
                kept = duplicate_of(row["job"])
                if kept is not None:
                    row["duplicate_of"] = kept
                    err_console.print(
                        f"Already uploaded: {row['path']} is a copy of "
                        f"{kept.get('title') or kept['artifact_id']} ({kept['artifact_id']}); "
                        "kept that file.",
                        highlight=False,
                        markup=False,
                    )
    waited = wait and bool(job_ids)
    knowledge_failed: list[dict[str, Any]] = []
    knowledge_settled = False
    knowledge_statuses: dict[str, str | None] = {}
    if wait_knowledge and project_id and all_uploaded(list(states.values()) if waited else []):
        knowledge_failed, knowledge_settled, knowledge_statuses = _wait_for_uploaded_knowledge(
            results, str(project_id), timeout=timeout
        )
    knowledge_incomplete = sorted(
        file_id for file_id, status in knowledge_statuses.items() if status in _NEEDS_RETRY
    )

    def render(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if row.get("refused"):
                continue
            artifact = row.get("artifact") or {}
            status = (row.get("job") or {}).get("status")
            kept = row.get("duplicate_of")
            if kept:
                console.print(
                    f"Already uploaded {row['path']} -> {kept['artifact_id']} "
                    f"(copy of {kept.get('title') or 'an existing file'})",
                    markup=False,
                )
                continue
            console.print(
                f"Uploaded {row['path']} -> {artifact.get('id')}"
                + _placement_note(row)
                + (f" ({status})" if status else ""),
                markup=False,
            )
        if knowledge_settled:
            note = (
                f"{len(knowledge_failed)} failed to reach Knowledge."
                if knowledge_failed
                else f"{len(knowledge_incomplete)} only partly in Knowledge."
                if knowledge_incomplete
                else "Indexed and in Knowledge."
            )
        elif waited:
            note = _KNOWLEDGE_NOTE.format(project=project_id or "<project>")
        elif ingest:
            note = "Indexing runs in the background."
        else:
            note = "Not sent to Knowledge (--no-knowledge)."
        sent = len(rows) - len(refused)
        console.print(f"[green]{sent} file(s) uploaded.[/green] {escape(str(note))}")
        if refused:
            console.print(f"[red]{len(refused)} file(s) not uploaded[/red] (see above).")
        if ingest and job_ids and not waited:
            console.print(f"[dim]Wait for it: beakr jobs wait {escape(' '.join(job_ids))}[/dim]")

    for row in refused:
        _print_refused(row, project_id=project_id)
    if wait_knowledge:
        failed_ids = {str(row.get("artifact_id")) for row in knowledge_failed}
        for row in results:
            file_id = _knowledge_file_id(row)
            failure = next(
                (f for f in knowledge_failed if str(f.get("artifact_id")) == file_id), None
            )
            if failure is not None:
                row["knowledge"] = {
                    "status": "failed",
                    "error_code": failure.get("error_code"),
                    "reason": failure.get("reason"),
                }
            elif knowledge_settled and file_id not in failed_ids:
                # The file's own knowledge_status, the word `beakr files ls` and
                # `files show` print for it (``created`` once it is in Knowledge).
                row["knowledge"] = {"status": knowledge_statuses.get(file_id)}
    emit(
        results,
        json_out=json_out,
        render=render,
        lines=_upload_lines,
        hint=_upload_hint(
            job_ids,
            ingest=ingest,
            waited=waited,
            failed=waited and not all_uploaded(list(states.values())),
            project_id=project_id,
            knowledge_failed=bool(knowledge_failed or knowledge_incomplete),
            knowledge_settled=knowledge_settled,
        ),
    )
    if refused:
        raise typer.Exit(1)
    if waited and not all_uploaded(list(states.values())):
        raise typer.Exit(1)
    if wait_knowledge and (knowledge_failed or knowledge_incomplete or not knowledge_settled):
        raise typer.Exit(1)


def authored_by(authors: list[str] | None) -> str | list[str] | None:
    """``--author`` as the API takes ``authored_by``: one author as given, several as a
    list (up to 20), and ``--author ""`` as ``""``, which clears them on an edit."""
    if authors is None:
        return None
    named = [author.strip() for author in authors if author.strip()]
    if not named:
        return ""
    return named[0] if len(named) == 1 else named


def authors_text(artifact: dict[str, Any]) -> str | None:
    """Every author of a file, ``Name <email>`` each, or its display line."""
    rows = [row for row in artifact.get("authors") or [] if isinstance(row, dict)]
    if rows:
        return "; ".join(
            f"{row.get('name')} <{row['email']}>" if row.get("email") else str(row.get("name"))
            for row in rows
        )
    return artifact.get("authored_by") or None


def _placement_options(
    on_existing: str | None,
    written_by_agent: bool,
    version_of: str | None = None,
    author: list[str] | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """The keyword arguments --on-existing, --written-by-agent, --version-of, --author
    and --date add.

    Only what was asked for: with neither flag nothing is added, and the server
    versions a file at the same path by default. The CLI's own ``files`` resource
    sends them on completion with any installed beakr-sdk
    (:mod:`beakr_cli.upload_placement`).
    """
    options: dict[str, Any] = {}
    if on_existing is not None:
        options["on_existing"] = on_existing
    if written_by_agent:
        options["written_by_agent"] = True
    if version_of is not None:
        options["version_of"] = version_of
    if author is not None:
        options["authored_by"] = authored_by(author)
    if date is not None:
        options["authored_at"] = date
    return options


#: What to run next for a refused placement, by refusal; ``{path}`` and ``{upload}``
#: name the file and its upload, which stays open with its bytes sent.
_REFUSED_NEXT = {
    "ambiguous_path": (
        "Add it to one of them: beakr upload {path} --resume {upload} --version-of FILE_ID "
        "(beakr files ls{project} --search {name} lists them), or keep it as a separate "
        "file: beakr upload {path} --resume {upload} --on-existing new"
    ),
    "file_not_writable": (
        "Ask for edit access to {artifact}, or keep yours as a separate file: "
        "beakr upload {path} --resume {upload} --on-existing new"
    ),
    "invalid_artifact": (
        "Check the id (beakr files ls{project}), then: "
        "beakr upload {path} --resume {upload} --version-of FILE_ID"
    ),
}


def _print_refused(row: dict[str, Any], *, project_id: str | None) -> None:
    """On stderr: why a file was not uploaded, and the command that finishes it."""
    refused = row["refused"]
    err_console.print(
        f"Not uploaded {row['path']}: {refused.get('message')}",
        highlight=False,
        markup=False,
        soft_wrap=True,
    )
    template = _REFUSED_NEXT.get(str(refused.get("code")))
    if template and refused.get("upload_id"):
        err_console.print(
            template.format(
                path=row["path"],
                upload=refused["upload_id"],
                name=Path(row["path"]).name,
                project=f" -P {project_id}" if project_id else "",
                artifact=refused.get("artifact_id") or "the file",
            ),
            highlight=False,
            markup=False,
            soft_wrap=True,
        )


def _placement_note(row: dict[str, Any]) -> str:
    """`` (new version 2)`` and the like: what the upload did to the file at its path."""
    result = row.get("result")
    version = row.get("version_no")
    if result == "versioned":
        return f" (new version {version})" if version else " (new version)"
    if result == "restored":
        return f" (version {version} is current again)" if version else " (earlier version)"
    if result == "unchanged":
        return " (unchanged: same as the current version)"
    return ""


#: A file's knowledge_status when its latest attempt saved only part of its
#: changes: settled but not wholly in Knowledge; `beakr projects retry` resumes it.
_NEEDS_RETRY = frozenset({"incomplete"})


def _wait_for_uploaded_knowledge(
    results: list[dict[str, Any]], project_id: str, *, timeout: float
) -> tuple[list[dict[str, Any]], bool, dict[str, str | None]]:
    """Wait for the project's Knowledge; return the uploaded files that failed, if it
    settled, and each uploaded file's own ``knowledge_status``.

    The project status is the read: the uploaded files are settled when nothing
    in the project is queued or running, which also waits out any other files
    compiling at the same time. Failures are reported only for these files.
    Each file's status is then read from the file itself, so the upload reports
    the word `beakr files ls` prints for it (an incomplete file is neither
    failed nor ready, and the project counts used to read ready over it).
    Inside an agent session, which may not read the project status, the files'
    own statuses are the read (:func:`_wait_for_files_knowledge`).
    """
    from beakr_cli.commands.project_knowledge import (
        failed_among,
        failed_file_line,
        wait_for_knowledge,
    )

    uploaded = {_knowledge_file_id(row) for row in results} - {""}
    err_console.print("Waiting for Knowledge...")
    with get_sdk(timeout=300.0) as sdk:
        try:
            status = wait_for_knowledge(sdk, project_id, timeout=timeout)
        except PermissionDeniedError as exc:
            if exc.code != _SESSION_ROUTE_UNAVAILABLE:
                raise
            settled = _wait_for_files_knowledge(sdk, project_id, uploaded, timeout=timeout)
            if settled is None:
                return [], False, {}
            failed, statuses = settled
        else:
            if status is None:
                return [], False, {}
            failed = failed_among(sdk, project_id, status, uploaded)
            statuses = {
                file_id: (sdk.files.get(file_id) or {}).get("knowledge_status")
                for file_id in sorted(uploaded)
            }
    for row in failed:
        err_console.print(f"Failed: {failed_file_line(row)}", highlight=False, markup=False)
    for file_id, file_status in statuses.items():
        if file_status in _NEEDS_RETRY:
            err_console.print(
                f"Incomplete: only part of its changes reached Knowledge ({file_id})",
                highlight=False,
                markup=False,
            )
    return failed, True, statuses


#: The engine's refusal of a route no agent session may call. The project's
#: Knowledge status is one (it is a web app view, not narrowed to the session's
#: projects), so inside Claude Code the wait reads the uploaded files instead.
_SESSION_ROUTE_UNAVAILABLE = "session_route_unavailable"

#: A file's knowledge_status while it is still on its way into Knowledge.
_PENDING = frozenset({"queued", "running"})


def _wait_for_files_knowledge(
    sdk: Any, project_id: str, file_ids: set[str], *, timeout: float
) -> tuple[list[dict[str, Any]], dict[str, str | None]] | None:
    """Wait until none of ``file_ids`` is queued or running for Knowledge, by
    reading each file's own ``knowledge_status`` (``GET /v1/artifacts/{id}``).

    The session-safe wait: a session may read its projects' files, but not the
    project's Knowledge status. Unlike the project wait it does not wait out
    other files compiling at the same time. Returns the failed files among them
    and each file's final status, or ``None`` after printing why when
    ``timeout`` passed first.
    """
    from beakr_cli.commands.project_knowledge import progress_line, state_phrase

    deadline = time.monotonic() + timeout
    interval = 5.0
    printed: list[str] = []
    while True:
        files = {file_id: sdk.files.get(file_id) or {} for file_id in sorted(file_ids)}
        statuses = {file_id: file.get("knowledge_status") for file_id, file in files.items()}
        counts = {
            word: sum(1 for status in statuses.values() if status == word)
            for word in ("queued", "running", "failed")
        }
        line = progress_line(counts)
        if not printed or printed[-1] != line:
            err_console.print(line, highlight=False, markup=False)
            printed.append(line)
        if not any(status in _PENDING for status in statuses.values()):
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            waiting = [file_id for file_id, status in statuses.items() if status in _PENDING]
            err_console.print(
                f"Knowledge is still {state_phrase(counts)} after {timeout:.0f}s; "
                f"check later with `beakr files show {waiting[0]}`.",
                highlight=False,
                markup=False,
            )
            return None
        time.sleep(min(interval, remaining))
        interval = min(interval * 1.5, 30.0)
    failed_ids = {file_id for file_id, status in statuses.items() if status == "failed"}
    failed: list[dict[str, Any]] = []
    if failed_ids:
        try:
            failed = [
                row
                for row in sdk.projects.knowledge_failures(project_id)
                if str(row.get("artifact_id")) in failed_ids
            ]
        except BeakrError:
            failed = []
        named = {str(row.get("artifact_id")) for row in failed}
        # A failure the list did not name is still reported, by the file's title.
        failed += [
            {"artifact_id": file_id, "filename": files[file_id].get("title") or file_id}
            for file_id in sorted(failed_ids - named)
        ]
    return failed, statuses


def _knowledge_file_id(row: dict[str, Any]) -> str:
    """The file whose Knowledge an uploaded row waits on: its own, or the copy it duplicated."""
    kept = row.get("duplicate_of")
    if isinstance(kept, dict) and kept.get("artifact_id"):
        return str(kept["artifact_id"])
    return str((row.get("artifact") or {}).get("id") or "")


#: What --wait waited for, and what still happens after it.
_KNOWLEDGE_NOTE = (
    "Indexed. Knowledge pages compile in the background, usually within a few "
    "minutes; `beakr kb ls -P {project}` to check."
)


def _upload_hint(
    job_ids: list[str],
    *,
    ingest: bool,
    waited: bool,
    failed: bool,
    project_id: str | None,
    knowledge_failed: bool = False,
    knowledge_settled: bool = False,
) -> str | None:
    if knowledge_failed:
        return f"beakr projects retry -P {project_id or '<project>'}"
    if knowledge_settled:
        return f"beakr kb ls -P {project_id or '<project>'}"
    if not ingest or not job_ids or failed:
        return None
    if waited:
        return f"beakr kb ls -P {project_id or '<project>'}  (Knowledge compiles in a few minutes)"
    return f"beakr jobs wait {' '.join(job_ids)}"


def _upload_lines(rows: list[dict[str, Any]]) -> list[str]:
    """``file_id<TAB>path<TAB>job=JOB_ID[<TAB>result=...<TAB>version=N][<TAB>status=...][...]``.

    ``result``/``version`` appear when the upload changed a file already at its
    path (``versioned``, ``restored``, ``unchanged``); a new file has neither.
    Then ``duplicate_of=``, ``knowledge=`` and ``knowledge_error=`` as they apply.
    """
    lines = []
    for row in rows:
        refused = row.get("refused")
        if refused:
            columns = ["-", str(row.get("path") or ""), f"refused={refused.get('code')}"]
            if refused.get("upload_id"):
                columns.append(f"upload={refused['upload_id']}")
            lines.append("\t".join(columns))
            continue
        artifact = row.get("artifact") or {}
        columns = [str(artifact.get("id") or "-"), str(row.get("path") or "")]
        if row.get("job_id"):
            columns.append(f"job={row['job_id']}")
        if row.get("result") and row["result"] != "created":
            columns.append(f"result={row['result']}")
            if row.get("version_no"):
                columns.append(f"version={row['version_no']}")
        job = row.get("job") or {}
        if job.get("status"):
            columns.append(f"status={job['status']}")
        error = job.get("error")
        if isinstance(error, dict) and (error.get("message") or error.get("code")):
            columns.append(f"error={error.get('message') or error.get('code')}")
        kept = row.get("duplicate_of")
        if isinstance(kept, dict) and kept.get("artifact_id"):
            columns.append(f"duplicate_of={kept['artifact_id']}")
        knowledge = row.get("knowledge") or {}
        if knowledge.get("status"):
            columns.append(f"knowledge={knowledge['status']}")
        if knowledge.get("error_code"):
            columns.append(f"knowledge_error={knowledge['error_code']}")
        lines.append("\t".join(columns))
    return lines


@app.command("ls")
def list_files(
    project: str | None = _project_opt,
    folder: str | None = typer.Option(None, "--folder", "-f", help="Folder id."),
    search: str | None = typer.Option(None, "--search", "-s", help="Title search."),
    waiting: bool = typer.Option(
        False,
        "--waiting",
        help="Only files waiting for a person to approve them into Knowledge "
        "(knowledge_status waiting_for_person).",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max files (1-200)."),
    json_out: bool = _json_opt,
) -> None:
    """List uploaded files."""
    with get_sdk() as sdk:
        page = sdk.files.list(
            project_id=resolve_project(project),
            folder_id=folder,
            search=search,
            # Filtered by the engine, so a project's held files are one page,
            # not a scan of every file.
            knowledge_status=_WAITING if waiting else None,
            limit=max(1, min(limit, 200)),
        )
    items = page.get("data", []) if isinstance(page, dict) else page

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No files wait for a person." if waiting else "No files.")
            return
        print_table(
            ["ID", "Title", "Type", "Knowledge", "Updated"],
            [
                [
                    str(a.get("id", "")),
                    short(a.get("title"), 48),
                    str(a.get("artifact_type") or ""),
                    str(a.get("knowledge_status") or ""),
                    str(a.get("updated_at") or "")[:10],
                ]
                for a in rows
            ],
            title="Files",
            whole=("ID",),
        )
        _waiting_note(rows)

    def lines(rows: list[dict[str, Any]]) -> list[str]:
        _waiting_note(rows)
        return [_file_line(row) for row in rows]

    emit(items, json_out=json_out, render=render, lines=lines)


#: A file an agent's key uploaded where the project requires a person to approve
#: it: stored and indexed, not compiled into Knowledge until approved.
_WAITING = "waiting_for_person"


def _file_line(row: dict[str, Any]) -> str:
    """The compact line of one file, with its ``knowledge=`` status when it has one."""
    line = summary_line(row)
    status = row.get("knowledge_status")
    return f"{line}\tknowledge={status}" if status and "\tknowledge=" not in line else line


def _waiting_note(rows: list[dict[str, Any]]) -> None:
    """On stderr: how many files wait for a person, and the command that approves one."""
    waiting = [row for row in rows if row.get("knowledge_status") == _WAITING]
    if waiting:
        err_console.print(
            f"{len(waiting)} file(s) wait for a person to approve them into Knowledge: "
            f"beakr files approve {waiting[0].get('id')}",
            highlight=False,
            markup=False,
            soft_wrap=True,
        )


@app.command("show")
def show_file(
    artifact_id: str = typer.Argument(..., help="File id."),
    json_out: bool = _json_opt,
) -> None:
    """Show a file's details."""
    with get_sdk() as sdk:
        artifact = sdk.files.get(artifact_id)

    def render(a: dict[str, Any]) -> None:
        console.print(f"[bold]{escape(str(a.get('title')))}[/bold] ({escape(str(a.get('id')))})")
        written_by = authors_text(a)
        if written_by:
            label = "Authors:" if len(a.get("authors") or []) > 1 else "Author:"
            console.print(f"{label:<11}{written_by}", markup=False)
        for label, key in (
            ("Type", "artifact_type"),
            ("Knowledge", "knowledge_status"),
            ("MIME", "mime_type"),
            ("Project", "home_project_id"),
            ("Written", "authored_at"),
            ("Version", "current_version_id"),
            ("Created", "created_at"),
            ("Updated", "updated_at"),
        ):
            if a.get(key):
                console.print(f"{label + ':':<11}{a[key]}", markup=False)
        if a.get("description"):
            console.print(f"\n{a['description']}", markup=False)
        if a.get("knowledge_status") == _WAITING:
            console.print(
                f"\nWaiting for a person to approve it into Knowledge: "
                f"beakr files approve {a.get('id')}",
                markup=False,
                soft_wrap=True,
            )

    emit(artifact, json_out=json_out, render=render)


@app.command("approve")
def approve_file(
    artifact_id: str = typer.Argument(..., help="File id (knowledge_status waiting_for_person)."),
    json_out: bool = _json_opt,
) -> None:
    """Approve a file an agent uploaded into Knowledge, and compile it.

    Where a project requires a person to approve agent uploads, a file an API
    key or token sent is stored and indexed but waits (knowledge_status
    waiting_for_person) until a person approves it. Only a signed-in person or
    a key holding proposals:accept may (exit 6 otherwise); a file not waiting
    exits 5.
    """
    with get_sdk() as sdk:
        result = sdk.files.approve_knowledge(artifact_id)

    def render(r: dict[str, Any]) -> None:
        status = r.get("knowledge_status") or r.get("queue_result") or "queued"
        name = f"{r['title']} ({artifact_id})" if r.get("title") else artifact_id
        console.print(f"Approved {name}: Knowledge {status}.", markup=False)

    emit(
        result,
        json_out=json_out,
        render=render,
        lines=lambda r: [_file_line(r)],
        hint=f"beakr files show {artifact_id}  (Knowledge compiles in a few minutes)",
    )


@app.command("download")
def download_file(
    artifact_id: str = typer.Argument(
        ...,
        help="File id, or any reference a read hands out: a source_ref, a version id, a "
        "citation token or a path.",
    ),
    # A str, not a Path: Path drops the trailing "/" that tells the SDK a
    # directory to create is meant (``-o downloads/``), and then the file is
    # written AS ``downloads``.
    out: str = typer.Option(
        ".", "--out", "-o", help="Destination file, or a directory (end a new one with /)."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Download a file's original bytes, by its id or a source record's source_ref."""
    with get_sdk(timeout=300.0) as sdk:
        saved = sdk.files.download_reference(artifact_id, out)
    emit(
        {"id": artifact_id, "path": str(saved), "bytes": Path(saved).stat().st_size},
        json_out=json_out,
        render=lambda d: console.print(f"Saved {d['path']} ({d['bytes']} bytes).", markup=False),
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
        render=lambda _: console.print(f"Deleted {artifact_id}.", markup=False),
    )


@app.command("edit")
def edit_file(
    artifact_id: str = typer.Argument(..., help="File id."),
    author: list[str] | None = typer.Option(
        None,
        "--author",
        metavar="NAME",
        help="Who wrote it: a name, 'Name <email>' or an email; repeat it for co-authors "
        "(it replaces every author). '' clears them.",
    ),
    date: str | None = typer.Option(
        None,
        "--date",
        metavar="DATE",
        help="When it was written: YYYY-MM-DD or an ISO 8601 date-time; '' clears it.",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Record who wrote a file and when, apart from who uploaded it.

    \b
    beakr files edit FILE_ID --author "Jane Doe <jane@example.com>" --date 2026-06-14
    beakr files edit FILE_ID --author "Jane Doe" --author "Bob Smith"
    beakr files edit FILE_ID --author ""
    """
    if author is None and date is None:
        raise click.UsageError("Give --author or --date (an empty value clears it).")
    with get_sdk() as sdk:
        artifact = sdk.files.set_authorship(
            artifact_id, authored_by=authored_by(author), authored_at=date
        )

    def render(a: dict[str, Any]) -> None:
        by = a.get("authored_by")
        at = a.get("authored_at")
        console.print(
            f"Saved {a.get('title') or artifact_id}: "
            + (f"written by {by}" if by else "no author recorded")
            + (f", {at}" if at else "")
            + ".",
            markup=False,
            highlight=False,
        )

    def lines(a: dict[str, Any]) -> list[str]:
        return [
            "\t".join(
                [
                    cell(a.get("id") or artifact_id),
                    f"authored_by={cell(a.get('authored_by') or '')}",
                    f"authored_at={a.get('authored_at') or ''}",
                    f"authors={len(a.get('authors') or [])}",
                ]
            )
        ]

    emit(artifact, json_out=json_out, render=render, lines=lines)


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
