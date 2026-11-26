"""A project's connected sources for the MCP tools: the text they answer and the listing.

The stdio halves of ``project_sources``, ``attach_source``, ``update_source``,
``preview_source_rules``, ``sync_source``, ``remove_source`` and ``tracked_items``
are in :mod:`beakr_cli.mcp_tools_projects`; the hosted halves are beakr-engine
``beakr/api/mcp_remote/source_tools.py``. Both servers answer the same text for
the same REST answer (the engine's ``test_mcp_every_tool_result_contract.py``),
so every function from ``source_line`` to ``removed_text`` is that file's,
character for character: change the wording there first, then here.

The REST work is the CLI's own client (``sdk.project_sources``, the routes
``beakr sources`` uses). The listing alone is read a page at a time through the
SDK's ``request``, because the tool reads a bounded number of pages and says so
when more exist, where ``sdk.project_sources.list`` follows every page; so is a
source's tracked items (``tracked_items_page``), which the released beakr-sdk
has no method for.
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
    "page id), item_type (folder, channel, page, database, label, ...) and "
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


def tracked_items_path(project_id: str, attachment_id: str) -> str:
    """The source's public tracked-items list (``GET .../tracked-items``)."""
    return f"/v1/projects/{segment(project_id)}/sources/{segment(attachment_id)}/tracked-items"


def history_import_path(project_id: str, attachment_id: str) -> str:
    """The source's public history import status (``GET .../history-import/status``)."""
    return (
        f"/v1/projects/{segment(project_id)}/sources/{segment(attachment_id)}/history-import/status"
    )


def history_import_status(sdk: Any, project_id: str, attachment_id: str) -> Any:
    """A source's history import status (awaitable from the async client), through ``request``."""
    return sdk.request("GET", history_import_path(project_id, attachment_id))


def recompile(sdk: Any, project_id: str, attachment_id: str, body: dict[str, Any]) -> Any:
    """Send a source's items to Knowledge again (awaitable), through the SDK's ``request``."""
    path = f"/v1/projects/{segment(project_id)}/sources/{segment(attachment_id)}/recompile"
    return sdk.request("POST", path, json=body)


def tracked_items_page(
    sdk: Any, project_id: str, attachment_id: str, params: dict[str, Any]
) -> Any:
    """One page of a source's tracked items (awaitable from the async client).

    Through the SDK's ``request``: the released beakr-sdk predates a typed method.
    """
    return sdk.request("GET", tracked_items_path(project_id, attachment_id), params=params)


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


def failing_text(source: dict[str, Any]) -> str:
    """Since when a source's syncs fail, why, and when one last worked; "" when healthy.

    ``last_sync_at`` and ``sync_status`` are the latest attempt, so a source
    that failed every night still read as freshly synced (Octant R10). The
    engine sets ``failing_since`` while every finished sync since the last
    success has failed; the CLI's ``beakr sources`` prints the same sentence.
    """
    since = source.get("failing_since")
    if not since:
        return ""
    reason = str(source.get("sync_failure_reason") or "").strip().rstrip(".")
    last_ok = source.get("last_successful_sync_at")
    return (
        f"Syncs have failed since {since}"
        + (f": {reason}." if reason else ".")
        + (f" Last successful sync: {last_ok}." if last_ok else " No sync has succeeded yet.")
    )


def source_line(source: dict[str, Any]) -> str:
    """One source on one line: what it is, its id, how it is set, and whether it fails."""
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
    failing = failing_text(source)
    return (
        f"- {name} ({source.get('provider')} {kind}) attachment_id {source.get('id')}: "
        f"{knowledge}, nightly sync {_on_off(source.get('auto_sync_wiki'))}, {synced}; "
        f"{_rules_text(source)}." + (f" {failing}" if failing else "")
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


#: Items a sync status names in text; the rest are counted, and tracked_items lists them.
STATUS_ITEMS_SHOWN = 10
#: Run outcomes, in the order a sync status counts them.
OUTCOME_ORDER = ("ingested", "partial", "skipped", "pending", "removed", "failed", "cancelled")


def _reason_text(item: dict[str, Any]) -> str:
    code = item.get("reason_code")
    reason = str(item.get("reason") or "").strip()
    if reason and code:
        return f": {reason} ({code})"
    if reason or code:
        return f": {reason or code}"
    return ""


def outcomes_text(outcomes: dict[str, Any]) -> list[str]:
    """What became of the items one sync brought in, and why any were set aside."""
    counts = _dict(outcomes.get("counts"))
    parts = [f"{counts[name]} {name}" for name in OUTCOME_ORDER if counts.get(name)]
    if not parts:
        return ["Items: none brought in by this sync yet."]
    lines = [f"Items: {', '.join(parts)}."]
    reasons = [
        f"{reason.get('reason_code') or 'no reason recorded'} {reason.get('count')}"
        for reason in (_dict(entry) for entry in outcomes.get("reasons") or [])
    ]
    if reasons:
        lines.append(f"Why: {', '.join(reasons)}.")
    listed = [
        _dict(item)
        for item in outcomes.get("items") or []
        if _dict(item).get("outcome") != "ingested"
    ][:STATUS_ITEMS_SHOWN]
    for item in listed:
        lines.append(f"- {item.get('title')} [{item.get('outcome')}]{_reason_text(item)}")
    set_aside = sum(int(counts.get(name) or 0) for name in OUTCOME_ORDER if name != "ingested")
    if set_aside > len(listed):
        lines.append(
            "tracked_items with this attachment_id lists every item and why it was set aside."
        )
    return lines


def sync_status_text(status: dict[str, Any]) -> str:
    state = status.get("status") or "never synced"
    done = status.get("files_completed")
    total = status.get("files_expected") or status.get("files_total")
    progress = f", {done} of {total} items" if total else ""
    failed = int(status.get("files_failed") or 0)
    line = f"Sync: {state}{progress}" + (f", {failed} failed." if failed else ".")
    outcomes = _dict(status.get("outcomes"))
    if not outcomes:
        return line
    return "\n".join([line, *outcomes_text(outcomes)])


#: A tracked item's states, in the order the counts print them (the route's
#: ``TrackedItemState``). partial: in Knowledge with planned changes that did not
#: land (#3311); failed: nothing of it reached Knowledge (#3278).
TRACKED_STATES = ("ingested", "partial", "pending", "skipped", "failed", "removed")


def _waiting_text(row: dict[str, Any]) -> str:
    """What a pending (or retried partial) item waits for, and when Beakr next acts on it."""
    waiting = str(row.get("pending_reason_text") or row.get("pending_reason") or "").strip()
    next_at = row.get("next_attempt_at")
    if not (waiting or next_at):
        return ""
    text = f"; waiting: {waiting.rstrip('.')}" if waiting else "; waiting"
    return text + (f" (next attempt {next_at})" if next_at else "")


def tracked_item_line(row: dict[str, Any]) -> str:
    """One tracked item: its title, state, key (what recompile_source takes) and why."""
    state = str(row.get("state") or "pending")
    changes = row.get("unfinished_changes")
    if state == "partial" and isinstance(changes, int):
        state = f"partial, {changes} change(s) not applied"
    line = f"- {row.get('title')} [{state}] external_id {row.get('external_id')}"
    line += _reason_text(row) + _waiting_text(row)
    if row.get("older_version_in_knowledge"):
        line += "; an older version is in Knowledge"
    return line


def tracked_items_text(page: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """One page of a source's tracked items, with the whole source's counts."""
    counts = _dict(page.get("counts"))
    total = int(counts.get("total") or 0)
    states = ", ".join(f"{int(counts.get(name) or 0)} {name}" for name in TRACKED_STATES)
    lines = [f"{total} tracked item(s) from {page.get('provider') or 'this source'}: {states}."]
    matched = page.get("matched")
    if isinstance(matched, int) and matched != total:
        lines.append(f"{matched} match the filters.")
    if not rows:
        lines.append("No tracked items match.")
    lines.extend(tracked_item_line(row) for row in rows)
    return "\n".join(lines)


def import_status_text(status: dict[str, Any]) -> str:
    """A source's history import in a line or two; "" when it takes none or none has run.

    The public import-status route (Octant R6, #3292): whether an import is
    waiting or running and where its items stand, else how the last one ended.
    """
    if not status.get("supported"):
        return ""
    state = str(status.get("status") or "idle")
    if state != "idle":
        line = f"History import {state}" + (
            " (a sync stopped partway; the next sync runs it again)"
            if status.get("retrying")
            else ""
        )
        outcome = _dict(status.get("outcome"))
        progress = _dict(status.get("progress"))
        if outcome.get("summary"):
            return f"{line}: {outcome['summary']}"
        if progress:
            return (
                f"{line}: {int(progress.get('tracked') or 0)} tracked, "
                f"{int(progress.get('brought_in') or 0)} read, "
                f"{int(progress.get('compiled') or 0)} compiled, "
                f"{int(progress.get('unprocessable') or 0)} set aside."
            )
        return f"{line}."
    last = _dict(status.get("last_outcome"))
    if not last.get("summary"):
        return ""
    cancelled = " (cancelled)" if status.get("last_import_cancelled") else ""
    return f"Last history import{cancelled}: {last['summary']}"


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


#: What the sync a recompile request started is doing (``SourceRecompileOut.sync``).
_RECOMPILE_SYNC = {
    "started": "A sync reading them has started",
    "waiting_for_running_sync": "A sync is already running; a new one reads them when it ends",
    "not_started": "No sync was started",
}


def recompile_text(answer: dict[str, Any]) -> str:
    """What sending a source's items to Knowledge again did, or would do (a dry run)."""
    mode = answer.get("mode") or "retry"
    requested = int(answer.get("requested") or 0)
    in_knowledge = int(answer.get("in_knowledge_left") or 0)
    in_progress = int(answer.get("in_progress_left") or 0)
    dry_run = bool(answer.get("dry_run"))
    verb = "Dry run: would send" if dry_run else "Sent"
    lines = [
        f"{verb} {requested} item(s) to Knowledge again (mode {mode}). Left out: "
        f"{in_knowledge} already in Knowledge, {in_progress} still on their way in."
    ]
    if in_knowledge:
        lines.append(
            f"The {in_knowledge} already in Knowledge at their current version were left "
            "alone; mode full sends them too and has each whole document read again."
        )
    if in_progress:
        lines.append(
            f"The {in_progress} still on their way into Knowledge (their compile is queued "
            "or running) were left out: send them again once they finish."
        )
    over = int(answer.get("over_cap") or 0)
    if over:
        lines.append(f"{over} more are past the 500-item limit: send them in a later call.")
    missing = [str(item) for item in answer.get("not_found") or []]
    if missing:
        lines.append(f"Not tracked by this source, nothing sent: {', '.join(missing)}.")
    if dry_run:
        lines.append(
            "Nothing was sent. Call again without dry_run, after the person agrees, to send them."
        )
        return "\n".join(lines)
    sync = str(answer.get("sync") or "")
    job = answer.get("sync_job_id")
    lines.append(_RECOMPILE_SYNC.get(sync, sync) + (f" (job {job})." if job else "."))
    if requested:
        lines.append(
            "project_sources with this attachment_id reports its progress; tracked_items shows "
            "each item's state."
        )
    return "\n".join(lines)


def removed_text(attachment_id: str) -> str:
    return (
        f"Removed the source {attachment_id} from the project. Knowledge already built from it "
        "is kept; it is no longer synced."
    )
