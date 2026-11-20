"""A project's sources: the connected items (a Drive folder, a Slack channel, a Notion
page or database) it pulls into Knowledge, over unlisted routes.

The engine serves ``/v1/projects/{project_id}/sources`` to API keys (scopes
``projects:read`` to read, ``integrations:admin`` to change) without documenting it
yet, so these calls live in the CLI's private client, not in beakr-sdk.

Attaching, changing and syncing a source are refused (403
``person_review_required``) with an agent's credential while the project requires a
person to approve what agents bring into Knowledge; removing one is not.

Example::

    src = beakr.project_sources.attach(
        project_id,
        provider="notion:nango",
        external_item_id="0123456789abcdef0123456789abcdef",
        external_item_type="page",
        external_item_name="Assay handbook",
        ingest_wiki=True,
    )
    beakr.project_sources.sync(project_id, src["id"])
    print(beakr.project_sources.status(project_id, src["id"])["status"])
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource

from beakr_cli._private_api._generated import operations as ops

#: Fields an edit can set back to nothing: ``update(..., clear=[...])`` sends them as
#: JSON null (an omitted field keeps its value).
CLEARABLE = frozenset({"source_query", "guidance"})


class ProjectSources(Resource[M]):
    @returns_all(ops.build_external_sources_list_project_sources)
    def list(self, project_id: str) -> Any:
        """Every source attached to the project, with its latest sync and your access to it."""
        return self._list_all(ops.build_external_sources_list_project_sources(project_id))

    def attach(
        self,
        project_id: str,
        *,
        provider: str,
        external_item_id: str,
        external_item_type: str,
        external_item_name: str,
        connector_id: str | None = None,
        external_item_path: str | None = None,
        external_item_url: str | None = None,
        ingest_wiki: bool | None = None,
        auto_sync_wiki: bool | None = None,
        scope_mode: str | None = None,
        source_query: dict[str, Any] | None = None,
        pull_mode: str | None = None,
        guidance: str | None = None,
    ) -> Any:
        """Attach one connected item to the project; it does not sync until :meth:`sync`.

        ``provider`` is the connection's provider (``gdrive:nango``, ``slack:nango``,
        ``notion:nango``...) and ``connector_id`` the connection to read it with (the
        engine takes your first usable one for the provider when it is left out).
        ``scope_mode="all"`` attaches everything the connection can read.
        The integration rules are sent only when given, so the call works with engines
        that predate them: ``source_query`` (exactly one of ``{"notion_filter": {...}}``
        or ``{"notion_view": "<view id>"}`` for a Notion database, or
        ``{"drive_q": "..."}`` for a Drive folder, drive or connection), ``pull_mode``
        (``"agent"``: Beakr's source picker chooses, the default; ``"all"``: every
        item in scope), and ``guidance`` (plain-language instructions the source
        picker and the Knowledge compiler both read).
        """
        body = clean_body(
            {
                "provider": provider,
                "connector_id": connector_id,
                "external_item_id": external_item_id,
                "external_item_type": external_item_type,
                "external_item_name": external_item_name,
                "external_item_path": external_item_path,
                "external_item_url": external_item_url,
                "ingest_wiki": ingest_wiki,
                "auto_sync_wiki": auto_sync_wiki,
                "scope_mode": scope_mode,
                "source_query": source_query,
                "pull_mode": pull_mode,
                "guidance": guidance,
            }
        )
        return self._run(ops.build_external_sources_attach_source_to_project(project_id, body=body))

    def update(
        self,
        project_id: str,
        attachment_id: str,
        *,
        ingest_wiki: bool | None = None,
        auto_sync_wiki: bool | None = None,
        source_query: dict[str, Any] | None = None,
        pull_mode: str | None = None,
        guidance: str | None = None,
        clear: tuple[str, ...] | list[str] = (),
    ) -> Any:
        """Change what was given; the rest keeps its value. It never starts a sync.

        ``clear`` names fields to set back to nothing (``source_query``, ``guidance``):
        they are sent as null. ``pull_mode`` cannot be cleared, only changed.
        """
        unknown = sorted(set(clear) - CLEARABLE)
        if unknown:
            raise ValueError(f"cannot clear {', '.join(unknown)}; clearable: {sorted(CLEARABLE)}")
        body: dict[str, Any] = clean_body(
            {
                "ingest_wiki": ingest_wiki,
                "auto_sync_wiki": auto_sync_wiki,
                "source_query": source_query,
                "pull_mode": pull_mode,
                "guidance": guidance,
            }
        )
        body.update({field: None for field in clear})
        return self._run(
            ops.build_external_sources_update_project_source(project_id, attachment_id, body=body)
        )

    @returns(ops.build_external_sources_delete_project_source)
    def remove(self, project_id: str, attachment_id: str) -> Any:
        """Detach the source; what Knowledge already took from it stays."""
        return self._run(
            ops.build_external_sources_delete_project_source(project_id, attachment_id)
        )

    @returns(ops.build_external_sources_sync_project_source)
    def sync(self, project_id: str, attachment_id: str) -> Any:
        """Start pulling the source into Knowledge now (``job_id``; 429 while cooling down)."""
        return self._run(ops.build_external_sources_sync_project_source(project_id, attachment_id))

    @returns(ops.build_external_sources_get_sync_status)
    def status(self, project_id: str, attachment_id: str) -> Any:
        """The latest sync's state and per-file progress."""
        return self._run(ops.build_external_sources_get_sync_status(project_id, attachment_id))
