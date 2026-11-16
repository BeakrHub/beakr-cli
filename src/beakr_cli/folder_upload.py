"""``beakr upload <folder>``: upload every file the server takes, and say which it refused.

The SDK's folder upload stopped at the first file the server refused for itself (an
instrument's ``.fcs`` export, a key-like file name): the files after it were never
sent, and nothing said which. This walks the folder the way the SDK does (same order,
same mirrored folders, same recorded paths) but skips a file refused for itself
(:data:`SKIPPED_FILE_CODES`, or an empty file) and goes on, as ``beakr mcp``'s
``upload_files`` does. A refusal about the credential, the project or the network
still stops the upload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Refusals of one file that say nothing about the others (``POST /v1/uploads`` and
#: its ``complete``).
SKIPPED_FILE_CODES = frozenset(
    {
        "unsupported_file_type",
        "invalid_path",
        "content_rejected",
        "size_mismatch",
        "upload_failed",
    }
)
EMPTY_FILE = "empty_file"


def upload_folder_skipping(
    sdk: Any,
    root: Path,
    *,
    project_id: str,
    folder_id: str | None,
    mirror_folders: bool,
    ingest: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Upload ``root``'s files; return the uploaded rows and the skipped files
    (``{"path", "name", "code", "message"}``), each in folder order."""
    return sdk.files._drive(  # type: ignore[no-any-return]
        _flow(
            root,
            project_id=project_id,
            folder_id=folder_id,
            mirror_folders=mirror_folders,
            ingest=ingest,
        )
    )


def _flow(
    root: Path, *, project_id: str, folder_id: str | None, mirror_folders: bool, ingest: bool
) -> Any:
    from beakr_sdk.errors import BeakrError
    from beakr_sdk.resources._upload import (
        _ensure_folder,
        iter_folder_files,
        upload_flow,
        upload_relative_path,
    )

    folder_ids: dict[Path, str | None] = {Path("."): folder_id}
    children: dict[str, dict[str, str]] = {}
    uploaded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for file_path in iter_folder_files(root):
        if file_path.stat().st_size == 0:
            skipped.append(_skip(file_path, EMPTY_FILE, "Beakr does not store empty files."))
            continue
        target = folder_id
        if mirror_folders:
            relative_dir = Path(root.name) / file_path.parent.relative_to(root)
            target = yield from _ensure_folder(relative_dir, folder_ids, project_id, children)
        try:
            row = yield from upload_flow(
                file_path,
                project_id=project_id,
                folder_id=target,
                ingest=ingest,
                relative_path=upload_relative_path(file_path, root),
            )
        except BeakrError as exc:
            if exc.code not in SKIPPED_FILE_CODES:
                raise
            skipped.append(_skip(file_path, str(exc.code), exc.message))
            continue
        uploaded.append({"path": str(file_path), **row})
    return uploaded, skipped


def _skip(path: Path, code: str, message: str) -> dict[str, Any]:
    return {"path": str(path), "name": path.name, "code": code, "message": message}


def summary_lines(uploaded: int, skipped: list[dict[str, Any]]) -> list[str]:
    """``Uploaded N of M files.`` and one ``Failed: <name>: <reason>`` line per skip."""
    lines = [f"Uploaded {uploaded} of {uploaded + len(skipped)} files."]
    lines += [f"Failed: {row['name']}: {row['message']}" for row in skipped]
    return lines
