"""File versions.

``versions`` lists a file's versions (``beakr files versions``) over
``GET /v1/artifacts/{artifact_id}/versions``. The route is documented now, so it
is not in the private allowlist; it is called here directly until the CLI locks
a beakr-sdk that has ``files.versions`` (beakr-python#123), which this then
defers to. The stdio MCP server's ``read_file`` reads text through the SDK's
documented ``files.read``.
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import ListCall
from beakr_sdk._paths import segment as _seg
from beakr_sdk._resource import M
from beakr_sdk.resources import files as _public


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
