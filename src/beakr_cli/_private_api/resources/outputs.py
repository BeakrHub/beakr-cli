"""Outputs: documents, sheets and decks Beakr produced, over unlisted routes.

The listing is the documented file listing narrowed to thread outputs; reading
an output, its revisions and its exports are unlisted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from beakr_sdk._calls import Flow, adjusted
from beakr_sdk._generated import operations as public_ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource

from beakr_cli._private_api._generated import operations as ops

#: Formats ``GET /v1/outputs/{id}/export/{format}`` produces.
EXPORT_FORMATS = ("pdf", "docx", "xlsx", "pptx", "md", "html")


class Outputs(Resource[M]):
    @returns(public_ops.build_files_list_artifacts)
    def list(
        self,
        *,
        project_id: str | None = None,
        search: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        """Outputs agents and workflows produced: ``{items, next_cursor, has_more}``.

        Outputs are the files a thread produced, so this is the file listing
        narrowed to thread outputs.
        """
        return self._run(
            public_ops.build_files_list_artifacts(
                project_id=project_id,
                search=search,
                limit=limit,
                cursor=cursor,
                thread_outputs=True,
            )
        )

    @returns(ops.build_canvas_get_canvas)
    def get(self, canvas_id: str) -> Any:
        """The output's metadata (title, type, current revision, project)."""
        return self._run(ops.build_canvas_get_canvas(canvas_id))

    @returns(ops.build_canvas_get_canvas_revisions)
    def revisions(
        self, canvas_id: str, *, limit: int | None = None, cursor: str | None = None
    ) -> Any:
        """One page of revisions, newest first: ``{data, next_cursor, has_more}``."""
        return self._run(
            ops.build_canvas_get_canvas_revisions(canvas_id, limit=limit, cursor=cursor)
        )

    @returns(ops.build_canvas_get_canvas_revision_content)
    def revision_content(self, canvas_id: str, revision_no: int) -> Any:
        return self._run(ops.build_canvas_get_canvas_revision_content(canvas_id, revision_no))

    def export(self, canvas_id: str, fmt: str, destination: str | Path, **options: Any) -> Any:
        """Write the output rendered as ``fmt`` (one of :data:`EXPORT_FORMATS`) to
        ``destination`` (a file path); returns the path.

        ``sources`` is ``"appendix"`` (the default: cited sources listed at the
        end) or ``"none"``.
        """
        target = Path(destination)

        def flow() -> Flow:
            data = yield adjusted(
                ops.build_canvas_export_canvas(canvas_id, fmt),
                params={k: v for k, v in options.items() if v is not None},
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return target

        return self._drive(flow())
