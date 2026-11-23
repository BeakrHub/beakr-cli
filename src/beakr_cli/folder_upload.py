"""``beakr upload <folder>``: upload every file the server takes, and say which it refused.

The SDK's folder upload stopped at the first file the server refused for itself (an
instrument's ``.fcs`` export, a key-like file name): the files after it were never
sent, and nothing said which. The CLI's folder upload
(:meth:`beakr_cli._private_api.resources.files.Files.upload_folder`) walks the folder
the way the SDK does (same order, same mirrored folders, same recorded paths) but
skips a file refused for itself (:data:`SKIPPED_FILE_CODES`, or an empty file) and
goes on, as ``beakr mcp``'s ``upload_files`` does. A refusal about the credential,
the project or the network still stops the upload.

``on_existing`` and ``written_by_agent`` go on each file's completion
(:mod:`beakr_cli.upload_placement`), and a file whose placement the server refuses
(:data:`~beakr_cli.upload_placement.REFUSED_PLACEMENT`: two files of that name in the
folder, or one the caller cannot change) stays among the uploaded rows as
``{"path", "refused"}``, as ``beakr upload`` reports it; the rest still go up.
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


class FolderRows(list):  # type: ignore[type-arg]
    """A folder upload's rows in folder order, with the files it skipped in ``skipped``
    (``{"path", "name", "code", "message"}``)."""

    def __init__(self) -> None:
        super().__init__()
        self.skipped: list[dict[str, Any]] = []


def upload_folder_skipping(
    sdk: Any,
    root: Path,
    *,
    project_id: str,
    folder_id: str | None,
    mirror_folders: bool,
    ingest: bool,
    on_existing: str | None = None,
    written_by_agent: bool | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Upload ``root``'s files; return the uploaded rows (with a ``refused`` row for a
    file whose placement was refused) and the skipped files
    (``{"path", "name", "code", "message"}``), each in folder order."""
    placement: dict[str, Any] = {}
    if on_existing is not None:
        placement["on_existing"] = on_existing
    if written_by_agent is not None:
        placement["written_by_agent"] = written_by_agent
    rows = sdk.files.upload_folder(
        root,
        project_id=project_id,
        folder_id=folder_id,
        mirror_folders=mirror_folders,
        ingest=ingest,
        **placement,
    )
    return list(rows), list(getattr(rows, "skipped", None) or [])


def skip_row(path: Path, code: str, message: str) -> dict[str, Any]:
    return {"path": str(path), "name": path.name, "code": code, "message": message}


def summary_lines(uploaded: int, skipped: list[dict[str, Any]]) -> list[str]:
    """``Uploaded N of M files.`` and one ``Failed: <name>: <reason>`` line per skip."""
    lines = [f"Uploaded {uploaded} of {uploaded + len(skipped)} files."]
    lines += [f"Failed: {row['name']}: {row['message']}" for row in skipped]
    return lines
