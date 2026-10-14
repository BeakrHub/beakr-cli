"""Interview requests, sessions and the interview maker, over unlisted routes."""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources import interviews as _public

from beakr_cli._private_api._generated import operations as ops


class Interviews(_public.Interviews[M]):
    """:class:`beakr_sdk.resources.interviews.Interviews` plus the CLI's unlisted calls."""

    def draft_sendable(
        self,
        message: str,
        *,
        draft: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
        evidence_artifact_ids: list[str] | None = None,
    ) -> Any:
        """One turn with the interview maker: Beakr proposes changes to a draft.

        ``draft`` holds ``title``, ``goals``, ``key_questions``,
        ``supporting_info`` and ``kind``; ``history`` the earlier turns as
        ``{"role": "person" | "maker", "text": ...}``. Returns ``reply``,
        ``changes`` (only the fields to change) and ``pending_files``. Nothing is
        saved: apply the changes and call :meth:`create_sendable`.
        """
        body = clean_body(
            {
                "message": message,
                "draft": draft,
                "history": history,
                "evidence_artifact_ids": evidence_artifact_ids,
            }
        )
        return self._run(ops.build_interview_sendables_ask_interview_maker(body=body))

    def send_request(
        self,
        to: str,
        *,
        project_id: str | None = None,
        goals: str | None = None,
        questions: list[str] | None = None,
        due: str | None = None,
        message: str | None = None,
        title: str | None = None,
    ) -> Any:
        """Ask one person for an interview; Beakr delivers it.

        ``to`` is an email address (anyone: Beakr makes a sendable interview
        and emails them its link) or a user id (a member of your organization:
        the request lands in their interview inbox, in ``project_id``, which is
        then required). ``due`` is a ``YYYY-MM-DD`` date. Returns
        ``delivery`` (``inbox`` or ``email``), the ``request_id`` or the
        ``sendable_id``/``link_id``/``email_status``, and for an email the link
        ``path`` once. Safe to retry: the create replays its first answer.
        """
        if not to or not to.strip():
            raise ValueError("to is an email address or a user id")
        to = to.strip()
        recipient = {"email": to} if "@" in to else {"user_id": to}
        body = clean_body(
            {
                "recipient": recipient,
                "project_id": project_id,
                "goals": goals,
                "questions": questions,
                "due": due,
                "message": message,
                "title": title,
            }
        )
        return self._run(ops.build_interview_create_interview_request(body=body))

    @returns_all(ops.build_interview_list_requests)
    def requests(self) -> Any:
        """Interview requests waiting for you."""
        return self._list_all(ops.build_interview_list_requests())

    @returns_all(ops.build_interview_list_sessions)
    def sessions(self, *, project_id: str | None = None) -> Any:
        return self._list_all(ops.build_interview_list_sessions(project_id=project_id))

    @returns(ops.build_interview_get_session_state)
    def session(self, session_id: str) -> Any:
        """A session with its note."""
        return self._run(ops.build_interview_get_session_state(session_id))
