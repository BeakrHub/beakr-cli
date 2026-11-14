"""Re-read a cited text-connector source through the provider's own read tool.

File connectors are re-read by a caller-scoped download handle
(``wiki_tools._caller_fetch_handle``). Text connectors have no download tool:
``granola_get_note`` takes a ``not_...`` note id, ``gmail_get_thread`` a thread
id, ``gcalendar_get_event`` a calendar and an event id. A cited Granola note used
to render with only its share link, the agent passed that link's uuid to
``granola_get_note``, Granola rejected it, and the run stopped without ever
reading the note it was asked to check (prod thread 271bb242, 2026-11-04).

The input is the user-independent ``provider_item_ref`` that
:mod:`beakr.services.units.native_item_refs` derives from the unit's identity
(``note:not_...``, ``gcal_event:<calendar>:<event>``). This module maps it to
an exact tool name and arguments; the caller check (does this caller have a
connector for the provider?) stays with the handle minting it mirrors.

Every argument here is one the tool's schema names. An unknown ref shape yields
``None`` -- an absent fetch line is honest, a guessed one costs a failed call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["NATIVE_FETCH_TOOLS", "NativeFetchCall", "native_fetch_call", "public_native_call"]

_GRANOLA_NOTE_ID = re.compile(r"not_[A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class NativeFetchCall:
    tool: str
    arguments: dict[str, Any]

    def instruction(self) -> str:
        """``granola_get_note with note_id=not_abc`` -- the rendered fetch line."""
        args = ", ".join(f"{name}={value}" for name, value in self.arguments.items())
        return f"{self.tool} with {args}"


def native_fetch_call(provider: str | None, item_ref: str | None) -> NativeFetchCall | None:
    """The exact read call for a text-connector item, or ``None``."""
    if not provider or not item_ref:
        return None
    kind, _, rest = item_ref.partition(":")
    if not rest:
        return None
    builder = _BUILDERS.get((provider.split(":", 1)[0], kind))
    return builder(rest) if builder else None


def _granola_note(rest: str) -> NativeFetchCall | None:
    # granola_get_note validates this exact shape and rejects anything else.
    if not _GRANOLA_NOTE_ID.fullmatch(rest):
        return None
    return NativeFetchCall("granola_get_note", {"note_id": rest})


def _gmail_thread(rest: str) -> NativeFetchCall | None:
    # ``thread:<thread>``, or a message / attachment unit beneath it
    # (``thread:<thread>:<message>[:attachment:<id>]``). The thread is the
    # readable unit in every case: it carries the message and names the
    # attachment.
    thread_id = rest.split(":", 1)[0]
    if not thread_id:
        return None
    return NativeFetchCall("gmail_get_thread", {"thread_id": thread_id})


def _gcalendar_event(rest: str) -> NativeFetchCall | None:
    # Event ids never contain a colon; the calendar id (an address) is
    # everything before the last one.
    calendar_id, _, event_id = rest.rpartition(":")
    if not calendar_id or not event_id:
        return None
    return NativeFetchCall(
        "gcalendar_get_event", {"calendar_id": calendar_id, "event_id": event_id}
    )


def _github_owner_repo(rest: str, *, parts: int) -> list[str] | None:
    pieces = rest.split("/", parts - 1)
    if len(pieces) != parts or not all(pieces):
        return None
    return pieces


def _github_issue(rest: str) -> NativeFetchCall | None:
    pieces = _github_owner_repo(rest, parts=3)
    if pieces is None or not pieces[2].isdigit():
        return None
    owner, repo, number = pieces
    return NativeFetchCall(
        "github_get_issue", {"owner": owner, "repo": repo, "issue_number": int(number)}
    )


def _github_pull_request(rest: str) -> NativeFetchCall | None:
    pieces = _github_owner_repo(rest, parts=3)
    if pieces is None or not pieces[2].isdigit():
        return None
    owner, repo, number = pieces
    return NativeFetchCall(
        "github_get_pull_request", {"owner": owner, "repo": repo, "pull_number": int(number)}
    )


def _github_file(rest: str) -> NativeFetchCall | None:
    pieces = _github_owner_repo(rest, parts=3)
    if pieces is None:
        return None
    owner, repo, path = pieces
    return NativeFetchCall("github_get_file", {"owner": owner, "repo": repo, "path": path})


def _slack_channel(rest: str) -> NativeFetchCall | None:
    # ``channel:<C>`` is the channel unit; ``channel:<C>:<ts>`` one message,
    # read with its thread.
    channel_id, _, ts = rest.partition(":")
    if not channel_id:
        return None
    if ts:
        return NativeFetchCall("slack_get_thread", {"channel_id": channel_id, "thread_ts": ts})
    return NativeFetchCall("slack_list_messages", {"channel_id": channel_id})


def _linear_issue(rest: str) -> NativeFetchCall | None:
    # linear_get_issue accepts the issue uuid the unit is keyed by.
    if ":" in rest:
        return None
    return NativeFetchCall("linear_get_issue", {"issue_id": rest})


_BUILDERS = {
    ("granola", "note"): _granola_note,
    ("gmail", "thread"): _gmail_thread,
    ("gcalendar", "gcal_event"): _gcalendar_event,
    ("github", "issue"): _github_issue,
    ("github", "pr"): _github_pull_request,
    ("github", "file"): _github_file,
    ("slack", "channel"): _slack_channel,
    ("linear", "issue"): _linear_issue,
}

#: Every provider read tool a native fetch call can name. The public source
#: contract forwards a ``retrieval`` only for these, with scalar arguments, so
#: nothing else a source record happens to carry reaches an outside caller.
NATIVE_FETCH_TOOLS = frozenset(
    {
        "granola_get_note",
        "gmail_get_thread",
        "gcalendar_get_event",
        "github_get_issue",
        "github_get_pull_request",
        "github_get_file",
        "slack_get_thread",
        "slack_list_messages",
        "linear_get_issue",
    }
)


def public_native_call(tool: Any, arguments: Any) -> dict[str, Any] | None:
    """A native fetch call in its public form, or ``None`` when it is not one.

    Outside agents read a cited Granola note, email thread or Slack thread
    through ``integrations command='call'``, which takes the provider tool's
    name and its own arguments. Research and Knowledge reads over MCP listed
    such a source by its share link only, so a caller passed the link's uuid to
    ``granola_get_note`` and was refused three times in one session (agent
    diary KBID-20261112-01a1d20c-006..008).
    """
    if not isinstance(tool, str) or tool not in NATIVE_FETCH_TOOLS:
        return None
    if not isinstance(arguments, dict) or not arguments:
        return None
    clean: dict[str, Any] = {}
    for name, value in arguments.items():
        if not isinstance(name, str) or isinstance(value, bool):
            return None
        if not isinstance(value, (str, int)) or value == "":
            return None
        clean[name] = value
    return {"tool": tool, "arguments": clean}
