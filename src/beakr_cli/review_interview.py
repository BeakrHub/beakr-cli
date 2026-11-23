"""Turn a project's open review flags into one interview, and say what was drafted.

One documented operation (beakr-engine #3109, ENG-2467):
``POST /v1/projects/{project_id}/interviews/from-reviews`` writes one
plain-language key question per open review flag (every kind by default, up to
30; the rest are listed as ``over_question_limit``) and saves the interview.
Without a participant it is a draft a person edits and sends in Beakr
(``edit_url``); with one (``{user_id}`` or ``{name, email}``) the interview also
gets that person's link (``link``, and ``link_url`` shown once). Beakr sends
nothing. ``201`` with the interview, or ``200`` with ``interview: null``,
``empty_reason`` and ``message`` when there is nothing to ask.

It is called through the SDK's ``request`` so the command and the MCP tool work
with the released beakr-sdk, which predates its typed method
(``interviews.draft_from_reviews`` in beakr-python).

:func:`review_interview_text` is the hosted ``draft_review_interview`` tool's
text (beakr-engine ``beakr/api/mcp_remote/review_interview_tools.py``), line for
line: both servers answer the same text for the same REST answer.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from beakr_sdk._paths import segment

#: A review flag's kinds (``other``: an older flag that names none).
FLAG_KINDS: tuple[str, ...] = (
    "contradiction",
    "ambiguity",
    "unverifiable",
    "likely_error",
    "other",
)
#: The most flags one interview asks about (its own question cap), and the default.
MAX_QUESTIONS = 30
#: The ``skipped`` reason of an open flag left out because the interview was full.
OVER_QUESTION_LIMIT = "over_question_limit"


def from_reviews_path(project_id: str) -> str:
    return f"/v1/projects/{segment(project_id)}/interviews/from-reviews"


def request_body(
    *,
    proposal_ids: Iterable[str] | None = None,
    kinds: Iterable[str] | None = None,
    max_questions: int | None = None,
    participant: dict[str, str | None] | None = None,
    link_expires_in_days: int | None = None,
    title: str | None = None,
    goals: str | None = None,
) -> dict[str, Any]:
    """The route's body with only what was given: blank text and empty lists are left out."""
    body: dict[str, Any] = {}
    flags = [str(flag) for flag in proposal_ids or ()]
    if flags:
        body["proposal_ids"] = flags
    chosen = list(kinds or ())
    if chosen:
        body["kinds"] = chosen
    if max_questions is not None:
        body["max_questions"] = max_questions
    person = {
        key: str(value).strip()
        for key, value in (participant or {}).items()
        if str(value or "").strip()
    }
    if person:
        body["participant"] = person
    if link_expires_in_days is not None:
        body["link_expires_in_days"] = link_expires_in_days
    for key, value in (("title", title), ("goals", goals)):
        if str(value or "").strip():
            body[key] = str(value).strip()
    return body


def draft_from_reviews(sdk: Any, project_id: str, body: dict[str, Any]) -> Any:
    """Make the interview (sync or async SDK client: awaitable from the async one)."""
    return sdk.request("POST", from_reviews_path(project_id), json=body)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def review_interview_text(answer: dict[str, Any]) -> str:
    """The draft as an agent reads it: the questions, their flags, and where to send it from."""
    interview = _dict(answer.get("interview"))
    skipped = [_dict(s) for s in answer.get("skipped") or []]
    skipped_line = (
        "Skipped: "
        + "; ".join(f"{s.get('proposal_id')} ({s.get('reason')})" for s in skipped)
        + "."
        if skipped
        else None
    )
    if not interview:
        lines = [f"Nothing drafted: {answer.get('message') or answer.get('empty_reason')}"]
        if skipped_line:
            lines.append(skipped_line)
        return "\n".join(lines)
    questions = [_dict(q) for q in answer.get("questions") or []]
    lines = [
        f'Drafted interview "{interview.get("title")}" (id {interview.get("id")}) with '
        f"{len(questions)} question(s), one per review flag. Nothing was sent."
    ]
    for question in questions:
        lines.append(f"{question.get('number')}. {question.get('question')}")
        lines.append(
            f"   Flag {question.get('proposal_id')} ({question.get('kind')}) on "
            f"{question.get('page_title')}: {question.get('reason')}"
        )
    if skipped_line:
        lines.append(skipped_line)
    lines.append(
        f"The person edits and sends it in Beakr: {answer.get('edit_url')}. Answers come "
        "back on each flag as suggestions; a person applies them."
    )
    return "\n".join(lines)
