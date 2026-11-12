"""Whether an upload waits for a person before it reaches Knowledge, and how to say so.

Where the project's agent review policy holds agent uploads for a person
(``agent_uploads_need_a_person``), a file an API key or token uploads is stored
and indexed but not compiled: it reads ``knowledge_status: waiting_for_person``
until a person approves it. The CLI and ``beakr mcp`` always upload with a key or
token, so under that policy every file they send waits; saying "Knowledge compiles
from them in the background" there was wrong (rc.9 QA).
"""

from __future__ import annotations

from typing import Any

#: A file's knowledge_status while it waits for a person's approval.
WAITING = "waiting_for_person"

#: Off only in the test suite, for tests of other upload behaviour (tests/conftest.py).
ENABLED = True


def waiting_line(file_ids: list[str]) -> str:
    """What an upload says when its files wait for a person's approval."""
    one = file_ids[0] if len(file_ids) == 1 else "<id>"
    who = "it" if len(file_ids) == 1 else "each file"
    return (
        f"Stored. A person must approve {who} before it's added to Knowledge (Files → the "
        f"file → Approve, or `beakr files approve {one}`)."
    )


def holds_uploads(settings: Any) -> bool:
    """Whether the policy in force (``GET /v1/knowledge/agent-review``) holds uploads."""
    effective = settings.get("effective") if isinstance(settings, dict) else None
    return isinstance(effective, dict) and effective.get("agent_uploads_need_a_person") is True


#: Most uploaded files whose own knowledge_status is read after an upload.
CHECK_LIMIT = 20


def waiting_ids(sdk: Any, project_id: str | None, file_ids: list[str]) -> list[str]:
    """The uploaded files that wait for a person's approval.

    Each file's own ``knowledge_status`` first (``GET /v1/artifacts/{id}``): the hold
    is recorded when the upload asks for Knowledge, and a session token may read its
    own project's file. The project's policy (``GET /v1/knowledge/agent-review``) is
    the fallback, but a session token is refused it: inside an agent session (rc.10
    QA) only the files' own status could say so.
    """
    from beakr_sdk.errors import BeakrError

    if not ENABLED or not file_ids:
        return []
    waiting: list[str] = []
    for file_id in file_ids[:CHECK_LIMIT]:
        try:
            status = (sdk.files.get(file_id) or {}).get("knowledge_status")
        except BeakrError:
            continue
        if status == WAITING:
            waiting.append(file_id)
    if waiting:
        return waiting
    return list(file_ids) if uploads_wait(sdk, project_id) else []


async def waiting_ids_async(call: Any, project_id: str | None, file_ids: list[str]) -> list[str]:
    """:func:`waiting_ids` for ``beakr mcp``: ``call`` runs one SDK request (``call_api``)."""
    if not ENABLED or not file_ids:
        return []
    waiting: list[str] = []
    for file_id in file_ids[:CHECK_LIMIT]:
        try:
            info = await call(lambda sdk, f=file_id: sdk.files.get(f))
        except Exception:  # noqa: BLE001 - one unreadable file says nothing
            continue
        if (info or {}).get("knowledge_status") == WAITING:
            waiting.append(file_id)
    if waiting:
        return waiting
    try:
        settings = await call(lambda sdk: sdk.knowledge.agent_review.get(project_id=project_id))
    except Exception:  # noqa: BLE001 - refused to a session token, or not offered
        return []
    return list(file_ids) if holds_uploads(settings) else []


def uploads_wait(sdk: Any, project_id: str | None) -> bool:
    """Whether files this client uploads to ``project_id`` wait for a person; False when
    the policy cannot be read (an installation without agent review, a key without the
    scope): the upload then says what it said before."""
    from beakr_sdk.errors import BeakrError

    if not ENABLED:
        return False
    try:
        return holds_uploads(sdk.knowledge.agent_review.get(project_id=project_id))
    except BeakrError:
        return False
