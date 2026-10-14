"""Connection coverage, sync and read scope, over unlisted routes."""

from __future__ import annotations

import builtins
from typing import Any

from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources import integrations as _public

from beakr_cli._private_api._generated import operations as ops


class Integrations(_public.Integrations[M]):
    """:class:`beakr_sdk.resources.integrations.Integrations` plus the CLI's unlisted calls."""

    @returns(ops.build_integrations_integration_coverage)
    def coverage(self, connector_id: str) -> Any:
        """Items known, indexed, compiled, and why the rest were not."""
        return self._run(ops.build_integrations_integration_coverage(connector_id))

    @returns(ops.build_integrations_sync_integration)
    def sync(self, connector_id: str, *, project_id: str | None = None) -> Any:
        """Sync the connection's project sources; each reports queued/cooldown/forbidden/refused."""
        return self._run(
            ops.build_integrations_sync_integration(connector_id, project_id=project_id or None)
        )

    @returns(ops.build_integrations_get_connector_scope)
    def get_scope(self, connector_id: str) -> Any:
        """What the connector may read: everything, or the listed folders/channels."""
        return self._run(ops.build_integrations_get_connector_scope(connector_id))

    def set_scope(
        self, connector_id: str, mode: str, *, items: builtins.list[dict[str, Any]] | None = None
    ) -> Any:
        """``mode`` is ``"all"`` or ``"restricted"``; ``restricted`` takes ``items``.

        Each item is ``{"id", "type", "name"}`` (``type`` is ``folder``, ``file``,
        ``page``, ``database``, ``drive`` or ``label``), optionally ``path`` and ``url``.
        """
        body: dict[str, Any] = {"mode": mode}
        if items is not None:
            body["items"] = items
        return self._run(ops.build_integrations_update_connector_scope(connector_id, body=body))
