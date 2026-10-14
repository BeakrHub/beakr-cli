"""Knowledge reads and the run undo the CLI uses, over unlisted routes."""

from __future__ import annotations

from typing import Any

from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources import knowledge as _public
from beakr_sdk.resources import knowledge_pages as _public_pages

from beakr_cli._private_api._generated import operations as ops


class KnowledgePages(_public_pages.KnowledgePages[M]):
    """:class:`beakr_sdk.resources.knowledge_pages.KnowledgePages` plus the CLI's unlisted calls."""

    @returns_all(ops.build_knowledge_get_wiki_page_backlinks)
    def backlinks(self, page_id: str) -> Any:
        """Every page that links to this one."""
        return self._list_all(ops.build_knowledge_get_wiki_page_backlinks(page_id))

    @returns(ops.build_knowledge_get_wiki_page_blame)
    def blame(self, page_id: str) -> Any:
        return self._run(ops.build_knowledge_get_wiki_page_blame(page_id))

    @returns(ops.build_knowledge_get_wiki_section_provenance)
    def section_provenance(self, page_id: str) -> Any:
        return self._run(ops.build_knowledge_get_wiki_section_provenance(page_id))

    @returns(ops.build_knowledge_get_wiki_timeline)
    def timeline(
        self,
        *,
        project_id: str | None = None,
        q: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page_type: str | None = None,
        include_content: bool | None = None,
        limit: int | None = None,
    ) -> Any:
        return self._run(
            ops.build_knowledge_get_wiki_timeline(
                project_id=project_id,
                q=q,
                start_date=start_date,
                end_date=end_date,
                page_type=page_type,
                include_content=include_content,
                limit=limit,
            )
        )

    @returns(ops.build_knowledge_get_wiki_graph)
    def graph(
        self,
        *,
        project_id: str | None = None,
        max_nodes: int | None = None,
        focus_page_id: str | None = None,
        org_only: bool | None = None,
    ) -> Any:
        return self._run(
            ops.build_knowledge_get_wiki_graph(
                project_id=project_id,
                max_nodes=max_nodes,
                focus_page_id=focus_page_id,
                org_only=org_only,
            )
        )

    @returns(ops.build_knowledge_get_stats)
    def stats(self, *, project_id: str | None = None) -> Any:
        return self._run(ops.build_knowledge_get_stats(project_id=project_id))


class Knowledge(_public.Knowledge[M]):
    """:class:`beakr_sdk.resources.knowledge.Knowledge` plus the CLI's unlisted calls."""

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.pages: KnowledgePages[M] = KnowledgePages(client)

    @returns(ops.build_knowledge_get_wiki_work_board)
    def work_board(self, *, project_id: str | None = None, label_id: str | None = None) -> Any:
        """Who is working on what in Knowledge right now (claimed pages and plans)."""
        return self._run(
            ops.build_knowledge_get_wiki_work_board(project_id=project_id, label_id=label_id)
        )

    @returns(ops.build_knowledge_list_knowledge_conflicts)
    def conflicts(
        self, *, scope: str | None = None, limit: int | None = None, cursor: str | None = None
    ) -> Any:
        """One page of card sections a source contradicts, with the sources on each side."""
        return self._run(
            ops.build_knowledge_list_knowledge_conflicts(scope=scope, limit=limit, cursor=cursor)
        )

    @returns(ops.build_knowledge_undo_knowledge_run)
    def undo_run(self, run: str, *, dry_run: bool = False) -> Any:
        """Take back one run's Knowledge writes: an Ask or workflow run id, or the
        ``run`` label a client sent (``Beakr(run=...)``, ``X-Beakr-Run``)."""
        return self._run(ops.build_knowledge_undo_knowledge_run(run, body={"dry_run": dry_run}))

    @returns(ops.build_knowledge_list_changes)
    def changes(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        agent: str | None = None,
        kind: list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        """What changed, newest first: Knowledge commits, proposals and files, with who
        (person or agent) made each. Page with ``cursor=result['next_cursor']``."""
        return self._run(
            ops.build_knowledge_list_changes(
                since=since,
                until=until,
                project_id=project_id,
                user_id=user_id,
                agent=agent,
                limit=limit,
                cursor=cursor,
                kind=list(kind) if kind else None,
            )
        )
