"""File versions, over an unlisted route.

``versions`` lists a file's versions (``beakr files versions``). The stdio MCP
server's ``read_file`` reads text through the SDK's documented ``files.read``.
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources import files as _public

from beakr_cli._private_api._generated import operations as ops


class Files(_public.Files[M]):
    """:class:`beakr_sdk.resources.files.Files` plus the CLI's unlisted calls."""

    @returns(ops.build_artifacts_list_artifact_versions)
    def versions(self, artifact_id: str) -> Any:
        return self._run(ops.build_artifacts_list_artifact_versions(artifact_id))
