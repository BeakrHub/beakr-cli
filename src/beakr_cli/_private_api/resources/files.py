"""File versions, and uploads that say what to do with a file already at their path.

``versions`` lists a file's versions (``beakr files versions``) over
``GET /v1/artifacts/{artifact_id}/versions``. The route is documented now, so it
is not in the private allowlist; it is called here directly until the CLI locks
a beakr-sdk that has ``files.versions`` (beakr-python#123), which this then
defers to. The stdio MCP server's ``read_file`` reads text through the SDK's
documented ``files.read``.

``upload``, ``upload_folder`` and ``resume_upload`` take ``on_existing``,
``written_by_agent`` and ``version_of`` with any beakr-sdk: they run the SDK's
own upload flows and add those fields to the completion
(:mod:`beakr_cli.upload_placement`), since the locked beakr-sdk (0.2.0) does not
send them. ``upload_folder`` goes on past a file whose placement the server
refuses, and returns that file's row with ``refused`` instead of stopping there.
It also skips, and lists in the result's ``skipped``, an empty file and a file the
server refuses for itself (:data:`beakr_cli.folder_upload.SKIPPED_FILE_CODES`), as
the self-hosted line's ``beakr upload <folder>`` always has.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from beakr_sdk._calls import Flow, ListCall
from beakr_sdk._paths import segment as _seg
from beakr_sdk._resource import M
from beakr_sdk.errors import BeakrError
from beakr_sdk.resources import files as _public
from beakr_sdk.resources._multipart_upload import multipart_upload_flow
from beakr_sdk.resources._upload import (
    _ensure_folder,
    detect_artifact_type,
    guess_mime_type,
    iter_folder_files,
    upload_flow,
    upload_relative_path,
)

from beakr_cli.folder_upload import (
    EMPTY_FILE,
    SKIPPED_FILE_CODES,
    FolderRows,
    skip_row,
)
from beakr_cli.upload_placement import placed, placement_fields, refused_placement


def refused_row(path: str | Path, exc: BeakrError) -> dict[str, Any]:
    """The row of a file whose placement the server refused (nothing was created)."""
    refused: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
        "upload_id": exc.details.get("upload_id"),
    }
    # The file already at the path (file_not_writable), or how many share its name.
    for key in ("artifact_id", "matching_files"):
        if key in exc.details:
            refused[key] = exc.details[key]
    return {"path": str(path), "refused": refused}


class Files(_public.Files[M]):
    """:class:`beakr_sdk.resources.files.Files` plus what the locked beakr-sdk lacks."""

    def versions(self, artifact_id: str) -> Any:
        return self._run(
            ListCall(
                "GET",
                f"/v1/artifacts/{_seg(artifact_id)}/versions",
                params={"cursor": None, "limit": None},
            )
        )

    def upload(
        self,
        path: str | Path,
        *,
        project_id: str | None = None,
        folder_id: str | None = None,
        title: str | None = None,
        artifact_type: str | None = None,
        description: str | None = None,
        ingest: bool = True,
        relative_path: str | None = None,
        on_existing: str | None = None,
        written_by_agent: bool | None = None,
        version_of: str | None = None,
    ) -> Any:
        """The SDK's ``files.upload``, completed with the placement options given."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Not a file: {file_path}")
        fields = placement_fields(
            on_existing=on_existing, written_by_agent=written_by_agent, version_of=version_of
        )
        return self._drive(
            placed(
                upload_flow(
                    file_path,
                    project_id=project_id,
                    folder_id=folder_id,
                    title=title,
                    artifact_type=artifact_type,
                    description=description,
                    ingest=ingest,
                    relative_path=relative_path or upload_relative_path(file_path),
                ),
                fields,
            )
        )

    def resume_upload(
        self,
        upload_id: str,
        path: str | Path,
        *,
        title: str | None = None,
        artifact_type: str | None = None,
        description: str | None = None,
        relative_path: str | None = None,
        on_existing: str | None = None,
        written_by_agent: bool | None = None,
        version_of: str | None = None,
    ) -> Any:
        """The SDK's ``files.resume_upload``, completed with the placement options given.

        Also completes an upload whose placement was refused, which stays open with
        every part sent: only the completion is sent again.
        """
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Not a file: {file_path}")
        mime_type = guess_mime_type(file_path)
        fields = placement_fields(
            on_existing=on_existing, written_by_agent=written_by_agent, version_of=version_of
        )
        return self._drive(
            placed(
                multipart_upload_flow(
                    file_path,
                    project_id=None,
                    title=title,
                    artifact_type=artifact_type or detect_artifact_type(mime_type, file_path.name),
                    description=description,
                    mime_type=mime_type,
                    upload_id=upload_id,
                    relative_path=relative_path or upload_relative_path(file_path),
                ),
                fields,
            )
        )

    def upload_folder(
        self,
        path: str | Path,
        *,
        project_id: str,
        folder_id: str | None = None,
        mirror_folders: bool = True,
        include_hidden: bool = False,
        ingest: bool = True,
        root_folder: bool = True,
        on_existing: str | None = None,
        written_by_agent: bool | None = None,
    ) -> Any:
        """The SDK's ``files.upload_folder``, completed with the placement options given.

        Returns ``[{"path", ...}]`` in upload order: each file's upload answer, or,
        for a file whose placement the server refused, ``{"path", "refused"}``
        (:func:`refused_row`). A re-upload into a folder that already holds two
        files of one name (made before uploads added versions) refuses only that
        file; the rest of the folder still goes up. An empty file, or one the
        server refuses for itself, is skipped and listed in the result's
        ``skipped`` (:class:`beakr_cli.folder_upload.FolderRows`).
        """
        root = Path(path)
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")
        fields = placement_fields(on_existing=on_existing, written_by_agent=written_by_agent)
        return self._drive(
            _folder_flow(
                root,
                project_id=project_id,
                folder_id=folder_id,
                mirror_folders=mirror_folders,
                include_hidden=include_hidden,
                ingest=ingest,
                root_folder=root_folder,
                fields=fields,
            )
        )


def _folder_flow(
    root: Path,
    *,
    project_id: str,
    folder_id: str | None,
    mirror_folders: bool,
    include_hidden: bool,
    ingest: bool,
    root_folder: bool,
    fields: dict[str, Any],
) -> Flow:
    """``upload_folder_flow`` of beakr-sdk, with placement fields and per-file refusals."""
    folder_ids: dict[Path, str | None] = {Path("."): folder_id}
    children: dict[str, dict[str, str]] = {}
    results = FolderRows()
    for file_path in iter_folder_files(root, include_hidden=include_hidden):
        if file_path.stat().st_size == 0:
            results.skipped.append(
                skip_row(file_path, EMPTY_FILE, "Beakr does not store empty files.")
            )
            continue
        relative_dir = file_path.parent.relative_to(root)
        if root_folder:
            relative_dir = Path(root.name) / relative_dir
        target_folder = folder_id
        if mirror_folders:
            target_folder = yield from _ensure_folder(
                relative_dir, folder_ids, project_id, children
            )
        try:
            uploaded = yield from placed(
                upload_flow(
                    file_path,
                    project_id=project_id,
                    folder_id=target_folder,
                    ingest=ingest,
                    relative_path=upload_relative_path(file_path, root),
                ),
                fields,
            )
        except BeakrError as exc:
            if refused_placement(exc):
                results.append(refused_row(file_path, exc))
                continue
            if exc.code not in SKIPPED_FILE_CODES:
                raise
            results.skipped.append(skip_row(file_path, str(exc.code), exc.message))
            continue
        results.append({"path": str(file_path), **uploaded})
    return results
