"""Stop a running source sync and queued Knowledge compiles, and read what is queued.

Three documented operations (beakr-engine #3078, ENG-2451):

- ``POST /v1/projects/{project_id}/sources/{attachment_id}/sync/cancel`` stops a
  source's running sync (``SyncCancelOut``; 409 ``no_running_sync`` when none is
  running). Items already being read finish; the rest are skipped.
- ``POST /v1/projects/{project_id}/knowledge/compiles/cancel`` skips the compiles
  still queued in a project, or for one source (``attachment_id``) or one file
  (``artifact_id``). Compiles already running finish. A source's cancel also
  stops that source's running sync, and a whole-project cancel every running sync
  in the project. 202 ``CompileCancelOut``.
- ``GET /v1/projects/{project_id}/knowledge/compiles`` counts the queued and
  running compiles, with the last cancel's progress (``CompileQueueOut``).

They are called through the SDK's ``request`` so the commands and MCP tools work
with the released beakr-sdk, which predates its typed methods for them
(``projects.cancel_source_sync``, ``knowledge.compile_queue`` and
``knowledge.cancel_queued_compiles`` in beakr-python).
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._paths import segment
from beakr_sdk.errors import BeakrError

#: The engine's refusal when a source has no sync running (409).
NO_RUNNING_SYNC = "no_running_sync"


def _source_body(attachment_id: str | None, artifact_id: str | None) -> dict[str, str]:
    if attachment_id and artifact_id:
        raise ValueError("Give a source or a file, not both.")
    if attachment_id:
        return {"attachment_id": attachment_id}
    if artifact_id:
        return {"artifact_id": artifact_id}
    return {}


def sync_cancel_path(project_id: str, attachment_id: str) -> str:
    return f"/v1/projects/{segment(project_id)}/sources/{segment(attachment_id)}/sync/cancel"


def compiles_path(project_id: str) -> str:
    return f"/v1/projects/{segment(project_id)}/knowledge/compiles"


def cancel_sync(sdk: Any, project_id: str, attachment_id: str) -> Any:
    """Stop a source's running sync (sync or async SDK client: awaitable from the async one)."""
    return sdk.request("POST", sync_cancel_path(project_id, attachment_id))


def compile_queue(
    sdk: Any, project_id: str, *, attachment_id: str | None = None, artifact_id: str | None = None
) -> Any:
    """How many compiles are queued and running, in the project or for one source or file."""
    params = _source_body(attachment_id, artifact_id)
    return sdk.request("GET", compiles_path(project_id), params=params or None)


def cancel_queued_compiles(
    sdk: Any, project_id: str, *, attachment_id: str | None = None, artifact_id: str | None = None
) -> Any:
    """Skip the queued compiles of the project, or of one source or file."""
    return sdk.request(
        "POST",
        f"{compiles_path(project_id)}/cancel",
        json=_source_body(attachment_id, artifact_id),
    )


def is_no_running_sync(exc: BaseException) -> bool:
    """Whether ``exc`` is the 409 that says the source has no sync running.

    The code is read from the error envelope (``error.code``) and, failing that,
    from a ``detail`` object (``{"detail": {"code": ...}}``); then the error's
    code and message are set from that object, so it prints as one sentence.
    """
    if not isinstance(exc, BeakrError) or exc.status != 409:
        return False
    if exc.code == NO_RUNNING_SYNC:
        return True
    try:
        body = exc.response.json() if exc.response is not None else None
    except ValueError:
        return False
    detail = body.get("detail") if isinstance(body, dict) else None
    if not (isinstance(detail, dict) and detail.get("code") == NO_RUNNING_SYNC):
        return False
    # Read as the envelope would have said it, not as the detail object's JSON.
    exc.code = NO_RUNNING_SYNC
    exc.message = str(detail.get("message") or "No sync is running for this source.")
    return True


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def sync_cancel_lines(answer: dict[str, Any], source: str) -> list[str]:
    """What a sync cancel did, in sentences."""
    job = answer.get("sync_job_id")
    state = answer.get("status") or "cancelling"
    head = (
        f"Stopping the sync of {source}"
        if state == "cancelling"
        else f"The sync of {source} is {state}"
    )
    out = [f"{head}{f' (job {job})' if job else ''}."]
    if answer.get("note"):
        out.append(str(answer["note"]))
    if answer.get("next_sync"):
        out.append(f"Next sync: {answer['next_sync']}")
    return out


def compile_cancel_lines(answer: dict[str, Any], where: str) -> list[str]:
    """What a compile cancel did, in sentences."""
    skipped = _count(answer.get("will_be_skipped"))
    running = _count(answer.get("already_running"))
    verb = "Cancelled" if answer.get("status") == "cancelled" else "Cancelling"
    out = [f"{verb} {_plural(skipped, 'queued compile')} {where}."]
    if running:
        out.append(
            f"{_plural(running, 'compile')} already running "
            f"{'is' if running == 1 else 'are'} left to finish."
        )
    if answer.get("sync_stopped"):
        job = answer.get("sync_job_id")
        stopped = max(_count(answer.get("syncs_stopped")), 1)
        out.append(
            f"Also stopped {_plural(stopped, 'running sync')}{f' (job {job})' if job else ''}: "
            "items not yet handed to Knowledge will not be queued."
        )
    if answer.get("note"):
        out.append(str(answer["note"]))
    if answer.get("next_sync"):
        out.append(f"Next sync: {answer['next_sync']}")
    return out


def queue_lines(answer: dict[str, Any], where: str) -> list[str]:
    """How many compiles are queued and running, and the last cancel's progress."""
    queued = _count(answer.get("queued"))
    running = _count(answer.get("running"))
    out = [
        f"{_plural(queued, 'compile')} queued and {running} running {where}."
        if queued or running
        else f"No compiles queued or running {where}."
    ]
    last = answer.get("last_cancel")
    if isinstance(last, dict) and last:
        by = f" by {last['cancelled_by']}" if last.get("cancelled_by") else ""
        at = f" at {last['cancelled_at']}" if last.get("cancelled_at") else ""
        summary = last.get("summary") or (
            f"{last.get('status') or 'cancelling'}: {_count(last.get('done'))} of "
            f"{_count(last.get('total'))}"
        )
        out.append(f"Last cancel{by}{at}: {summary}")
    return out


# -- the MCP tools' text -----------------------------------------------------------------
#
# ``knowledge_queue``, ``cancel_compiles`` and ``cancel_sync`` answer exactly what the
# hosted server answers for the same REST answer (beakr-engine
# ``beakr/api/mcp_remote/queue_tools.py``; its
# ``tests/unit/test_mcp_every_tool_result_contract.py`` holds both servers to it).


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def queue_text(queue: dict[str, Any], *, source: bool) -> str:
    where = "this source" if source else "this project"
    queued = int(queue.get("queued") or 0)
    running = int(queue.get("running") or 0)
    lines = [f"Knowledge compiles for {where}: {queued} queued, {running} running."]
    last = _dict(queue.get("last_cancel"))
    if last:
        by = last.get("cancelled_by") or "someone"
        lines.append(f"Last cancel: {last.get('summary')} (by {by}).")
    if queued:
        lines.append(
            "cancel_compiles cancels the queued ones, after the person agrees; running ones finish."
        )
    return "\n".join(lines)


def cancel_compiles_text(answer: dict[str, Any]) -> str:
    skipped = int(answer.get("will_be_skipped") or 0)
    running = int(answer.get("already_running") or 0)
    lines = [
        f"{skipped} queued compiles will not run ({answer.get('status')}). "
        f"{running} already running will finish."
    ]
    if answer.get("sync_stopped"):
        lines.append(
            f"Also cancelled {int(answer.get('syncs_stopped') or 0)} running sync(s): items "
            "they had not handed off will not queue."
        )
    for key in ("next_sync", "note"):
        if answer.get(key):
            lines.append(str(answer[key]))
    lines.append(f"cancel_id: {answer.get('cancel_id')}. knowledge_queue reports progress.")
    return "\n".join(lines)


def cancel_sync_text(answer: dict[str, Any]) -> str:
    lines = [
        f"Cancelling the sync (job {answer.get('sync_job_id')}): listing stops at its next "
        "step and items not yet started are skipped. Compiles already queued still run; "
        "cancel_compiles with this attachment_id cancels them."
    ]
    for key in ("next_sync", "note"):
        if answer.get(key):
            lines.append(str(answer[key]))
    return "\n".join(lines)
