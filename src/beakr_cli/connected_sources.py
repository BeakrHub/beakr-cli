"""A project's connected sources for the MCP tools: the text they answer and the listing.

The stdio halves of ``project_sources``, ``attach_source``, ``update_source``,
``preview_source_rules``, ``sync_source`` and ``remove_source`` are in
:mod:`beakr_cli.mcp_tools_projects`; the hosted halves are beakr-engine
``beakr/api/mcp_remote/source_tools.py``. Both servers answer the same text for
the same REST answer (the engine's ``test_mcp_every_tool_result_contract.py``),
so every function from ``source_line`` to ``removed_text`` is that file's,
character for character: change the wording there first, then here.

The REST work is the CLI's own client (``sdk.project_sources``, the routes
``beakr sources`` uses). The listing alone is read a page at a time through the
SDK's ``request``, because the tool reads a bounded number of pages and says so
when more exist, where ``sdk.project_sources.list`` follows every page.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from beakr_sdk._paths import segment

#: Page of sources read per call, and the most pages a listing follows.
PAGE = 100
PAGES = 10

#: The refusal for a call that names no source.
NAME_SOURCE = (
    "invalid_arguments",
    "Name the source by its attachment_id, a UUID.",
    "Pass attachment_id exactly as project_sources prints it.",
)
#: The refusal for an attachment_id the project's listing does not hold.
NOT_ATTACHED = (
    "not_found",
    "No source with that attachment_id is attached to this project.",
    "Call project_sources without attachment_id to list them.",
)
#: The refusal for an attach that leaves out what the route requires.
ATTACH_NEEDS = (
    "invalid_arguments",
    "Attaching needs the provider, the item's id, its type and its name.",
    "Pass provider (as integrations command='list' prints it, e.g. 'gdrive:nango'), "
    "item_id (the service's own id: a Drive folder id, a Slack channel id, a Notion "
    "page id), item_type (folder, file, channel, page, database, label, ...) and "
    "name (what to call it in the project).",
)
#: The refusal for an update that changes nothing.
NOTHING_TO_CHANGE = (
    "invalid_arguments",
    "Nothing to change.",
    "Pass at least one of knowledge, nightly_sync, filter, guidance, clear_filter "
    "or clear_guidance.",
)


def attachment_id(value: Any) -> str:
    """The source's attachment id, or "" unless it is a UUID.

    The id becomes a path segment of the request made as the signed-in person,
    so anything else (a slash, dots, a query) could name a different route.
    """
    given = str(value or "").strip()
    try:
        return str(UUID(given)) if len(given) == 36 else ""
    except ValueError:
        return ""


def sources_page(sdk: Any, project_id: str, cursor: str | None = None) -> Any:
    """One page of the project's sources (awaitable from the async client)."""
    params: dict[str, Any] = {"limit": PAGE}
    if cursor:
        params["cursor"] = cursor
    return sdk.request("GET", f"/v1/projects/{segment(project_id)}/sources", params=params)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _on_off(value: Any) -> str:
    return "on" if value else "off"


def _rules_text(source: dict[str, Any]) -> str:
    parts = []
    query = source.get("source_query")
    if isinstance(query, dict) and query:
        parts.append(f"filter {query}")
    guidance = str(source.get("guidance") or "").strip()
    if guidance:
        parts.append(f"guidance {guidance!r}")
    return "; ".join(parts) or "no filter or guidance"


def source_line(source: dict[str, Any]) -> str:
    """One source on one line: what it is, its id, and how it is set."""
    name = source.get("external_item_name") or source.get("external_item_id")
    kind = source.get("external_item_type") or "item"
    last = source.get("last_sync_at")
    state = source.get("sync_status")
    synced = f"last synced {last}" if last else "never synced"
    if state == "syncing":
        synced = "syncing now"
    knowledge = (
        f"Knowledge {_on_off(source.get('ingest_wiki'))}"
        if source.get("supports_wiki_ingest", True)
        else "not read into Knowledge"
    )
    return (
        f"- {name} ({source.get('provider')} {kind}) attachment_id {source.get('id')}: "
        f"{knowledge}, nightly sync {_on_off(source.get('auto_sync_wiki'))}, {synced}; "
        f"{_rules_text(source)}."
    )


def sources_text(sources: list[dict[str, Any]], *, more: bool) -> str:
    if not sources:
        return (
            "This project has no connected sources. attach_source adds one from a "
            "connected integration."
        )
    lines = [f"{len(sources)} connected source(s):", *[source_line(item) for item in sources]]
    if more:
        lines.append("More sources exist than are listed here.")
    return "\n".join(lines)


def sync_status_text(status: dict[str, Any]) -> str:
    state = status.get("status") or "never synced"
    done = status.get("files_completed")
    total = status.get("files_expected") or status.get("files_total")
    progress = f", {done} of {total} items" if total else ""
    failed = int(status.get("files_failed") or 0)
    return f"Sync: {state}{progress}" + (f", {failed} failed." if failed else ".")


def attached_text(source: dict[str, Any]) -> str:
    lines = ["Attached.", source_line(source)]
    if not source.get("ingest_wiki"):
        lines.append(
            "Knowledge is off for it, so nothing has been read. update_source with "
            "knowledge=true turns it on and starts the first sync, after the person agrees: "
            "that reads the source and spends the organization's ingest budget. Set a filter "
            "or guidance first if the source is large (preview_source_rules shows what a "
            "filter keeps)."
        )
    else:
        lines.append("Knowledge is on. sync_source starts the first sync.")
    return "\n".join(lines)


def updated_text(source: dict[str, Any], *, turned_on: bool) -> str:
    lines = ["Saved.", source_line(source)]
    if turned_on:
        lines.append("Knowledge was turned on. sync_source starts the first sync now.")
    return "\n".join(lines)


def preview_text(preview: dict[str, Any]) -> str:
    if preview.get("provider_status") == "rejected":
        return f"The service refused this filter: {preview.get('message')}"
    count = int(preview.get("count") or 0)
    at_least = "At least " if preview.get("count_capped") else ""
    lines = [f"{at_least}{count} item(s) match under filter {preview.get('source_query')}."]
    for item in (preview.get("sample") or [])[:10]:
        item = _dict(item)
        lines.append(f"- {item.get('title') or item.get('name') or item.get('id')}")
    for group in preview.get("breakdown") or []:
        group = _dict(group)
        lines.append(f"  {group.get('name') or group.get('id')}: {group.get('count')}")
    if preview.get("message"):
        lines.append(str(preview["message"]))
    dry_run = _dict(preview.get("dry_run"))
    if dry_run:
        lines.append(
            f"A sync would bring in {dry_run.get('would_ingest', dry_run.get('picked_count'))} "
            "of them."
        )
    if count == 0:
        lines.append("Nothing matches, so a sync would bring nothing in. Loosen the filter.")
    return "\n".join(lines)


def sync_started_text(answer: dict[str, Any]) -> str:
    message = answer.get("message") or answer.get("status") or "started"
    return (
        f"Sync started ({message}). project_sources with this attachment_id reports its "
        "progress; cancel_sync stops it."
    )


def removed_text(attachment_id: str) -> str:
    return (
        f"Removed the source {attachment_id} from the project. Knowledge already built from it "
        "is kept; it is no longer synced."
    )
