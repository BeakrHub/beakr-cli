"""Session project scopes: the projects one agent session (or shell) may read and write.

A person chooses which of their projects a session may read and which it may
write; the API confines every request made with the session's token
(``bk_st_...``) to those lists, read again on each request, so a change the
person makes applies to the session's next call. The agent holding the token can
only drop projects, never add them (``403 session_widen_refused``).

Two holders use it here:

- **``beakr mcp``** (:class:`McpSession`): the server starts a session when it
  starts, holds the token in memory, re-mints it before it expires with the
  person's own credential, and ends the session on a clean exit. Until the person
  chooses, tools answer with the picker link (or, where the client supports MCP
  form elicitation, an in-chat form). On an installation without sessions (``404``
  on ``POST /v1/sessions``) it runs as before, with the person's credential.
- **A shell** (``beakr session start``): the session's token is kept in
  ``~/.beakr/sessions/<id>.json`` (0600), and ``BEAKR_SCOPE_SESSION=<id>`` names
  the session for the commands run in that shell. The id is not a secret, so it
  can sit in the environment and be printed for ``eval``; the token never leaves
  the file. ``BEAKR_SESSION`` is not used for this: it already labels the agent
  session a request belongs to (``X-Beakr-Session``).

Folder memory: when the person ticks "remember for this folder" on the picker
(``remember_folder``), the selection is saved in the folder's binding
(``.beakr/project.json``, ``beakr init``), and the next session started in that
folder is created with it. It is still shown, and editable, on the picker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from beakr_sdk.errors import BeakrError

#: Names the shell session the commands in this shell use (its id, not its token).
SESSION_ENV = "BEAKR_SCOPE_SESSION"
#: ``off`` keeps ``beakr mcp`` on the person's own credential, without a session.
MCP_SESSIONS_ENV = "BEAKR_MCP_SESSIONS"
#: The binding key a remembered selection is kept under.
BINDING_FIELD = "session_scope"
#: Seconds a token is minted for, and how early it is renewed.
TOKEN_TTL_SECONDS = 3600
RENEW_MARGIN_SECONDS = 300
#: Most projects a session may name (the API refuses longer lists with 422).
MAX_PROJECTS = 200

NEEDS_SELECTION = "session_needs_selection"
WIDEN_REFUSED = "session_widen_refused"
WRITE_SCOPE = "session_write_scope"
SESSION_ENDED = "session_ended"
#: What :meth:`McpSession.recover` did after the API refused the session's token.
RENEWED = "renewed"
REPLACED = "replaced"
#: Said with the picker (form or link) when ``beakr mcp`` replaced an ended session.
REPLACED_MESSAGE = "The previous Beakr session was ended; choose projects for a new one."
#: What every session token starts with.
SESSION_TOKEN_PREFIX = "bk_st_"

# Project boundaries (each project's ``boundary``, and the organization's settings,
# ``GET /v1/organizations/{org_id}/project-isolation``).
#: Session modes (``mode`` on every session). automatic: it starts open and the
#: server tracks what it touches; picked: its read and write lists were chosen at
#: the start; read_everything: it reads every project the person can, writes none.
AUTOMATIC = "automatic"
PICKED = "picked"
READ_EVERYTHING = "read_everything"
#: What an engine before project boundaries called automatic and picked.
AUTO_LOCK = "auto_lock"
PICKER = "picker"
#: The organization's ``session_mode``: automatic, or every session must pick.
MUST_PICK = "must_pick"
#: Rule A: a session that touched one locked project writes only it, two make it
#: read-only (and a read_everything session never writes).
LOCKED_TO_PROJECT = "session_locked_to_project"
READ_ONLY = "session_read_only"
#: Picked lists that break a boundary, refused at the start (``reasons[]`` says why).
SELECTION_INVALID = "session_selection_invalid"
#: Declaring (or changing the mode of) a session that has already touched a project.
ALREADY_STARTED = "session_already_started"
#: A write that brings a locked project's content into another project.
CONTENT_LOCKED = "project_content_locked"
#: The organization requires an agent's writes to run in a session.
SESSION_REQUIRED = "session_required"
#: A call in a session that must name a project named none (engine #2651; it used
#: to answer ``insufficient_scope``, whose fix pointed at a missing key scope).
PROJECT_REQUIRED = "session_project_required"
#: The project written does not take content from the other projects in play.
GUARD_REFUSED = "cross_project_write_refused"
GUARD_OWNER_REQUIRED = "cross_project_owner_required"
#: Refusals of what an engine before project boundaries' auto_lock session did not
#: take (project lists; never answered now) or an installation without agent
#: sessions (``agent_session``).
MODE_AUTO_LOCK = "session_mode_auto_lock"
FEATURE_NOT_ENABLED = "feature_not_enabled"
#: The fix when an agent conversation's session can no longer be used: its id
#: never starts another session, so only a new conversation gets a fresh one.
NEW_CONVERSATION_FIX = (
    "Start a new agent conversation (a new Claude Code session, a new Codex thread): it gets "
    "a new Beakr session. Work in one project per conversation."
)


class SessionScopeError(BeakrError):
    """A session operation refused locally, with the fix (shown like any API refusal)."""

    def __init__(self, message: str, *, code: str = "session_error", fix: str | None = None):
        super().__init__(message, code=code, fix=fix)

    @property
    def fields(self) -> dict[str, str | None]:
        return {"error_code": self.code, "message": self.message, "fix": self.fix}


# --- Wire helpers ---------------------------------------------------------------------


def picker_url_of(exc: BeakrError) -> str | None:
    """The picker link a ``session_needs_selection`` (or write) refusal carries."""
    for source in (exc.details, _detail(exc.body)):
        if isinstance(source, dict) and isinstance(source.get("picker_url"), str):
            return source["picker_url"]
    return None


def _detail(body: Any) -> Any:
    return body.get("detail") if isinstance(body, dict) else None


def project_ids(projects: list[dict[str, Any]] | None) -> list[str]:
    return [str(p.get("id")) for p in projects or [] if p.get("id")]


def mode_of(session: dict[str, Any]) -> str | None:
    """The session's mode, an older engine's ``auto_lock``/``picker`` read as today's."""
    mode = session.get("mode")
    return {AUTO_LOCK: AUTOMATIC, PICKER: PICKED}.get(str(mode), mode) if mode else None


def automatic(session: dict[str, Any]) -> bool:
    """Whether the session starts open and the server tracks what it touches."""
    return mode_of(session) == AUTOMATIC


def reads_everything(session: dict[str, Any]) -> bool:
    """Whether the session reads every project the person can, and writes none."""
    return mode_of(session) == READ_EVERYTHING


def lock_bound(session: dict[str, Any]) -> bool:
    """Whether a refused write is the lock's doing (or read everything's), not the lists'
    a person can widen at the picker."""
    return (
        automatic(session)
        or reads_everything(session)
        or session.get("lock_state") in {"locked", "read_only"}
    )


def _project_name(project: Any) -> str:
    return str(project.get("name") or project.get("id")) if isinstance(project, dict) else ""


def may_write(session: dict[str, Any], project_id: str) -> bool:
    """Whether the session may write ``project_id`` now, as the API will decide it.

    Picked: its write list. Automatic: any project while open (it reports
    ``write_projects: []`` then). In either, once it touched a locked project
    (``lock_state: locked``) only that project, and once read-only none; a
    read_everything session writes none. Content in (a project that takes no
    content from others) is the API's to decide.
    """
    state = session.get("lock_state")
    if reads_everything(session) or state == "read_only":
        return False
    if state == "locked":
        locked = session.get("write_project")
        if isinstance(locked, dict) and locked.get("id"):
            return project_id == str(locked["id"])
        return project_id in project_ids(session.get("write_projects"))
    if automatic(session):
        return True
    return project_id in project_ids(session.get("write_projects"))


def lock_refusal(session: dict[str, Any], what: str) -> SessionScopeError:
    """A locked, read-only or read_everything session's refusal of a write to ``what``
    (project names), said before anything is sent, with the fix that works here."""
    session_id = session.get("id")
    fix = restart_fix(session)
    if reads_everything(session):
        return SessionScopeError(
            f"Session {session_id} reads everything, so it cannot write {what}; nothing was "
            "written.",
            code=READ_ONLY,
            fix=fix,
        )
    if session.get("lock_state") == "read_only":
        touched = ", ".join(_project_name(p) for p in session.get("touched_projects") or [])
        reason = session.get("read_only_reason") or f"It read {touched or 'two locked projects'}."
        return SessionScopeError(
            f"Session {session_id} is read-only: {reason} It cannot write {what}; nothing was "
            "written.",
            code=READ_ONLY,
            fix=fix,
        )
    locked = _project_name(session.get("write_project")) or ", ".join(
        _project_name(p) for p in session.get("write_projects") or []
    )
    return SessionScopeError(
        f"Session {session_id} is locked to {locked}: it can write only to {locked}, so it "
        f"cannot write {what}; nothing was written.",
        code=LOCKED_TO_PROJECT,
        # The project is named in the message: the fix is only what to do (a "To write
        # B:" lead-in read "To fix: To write B: ..." on one line, rc.9 QA).
        fix=fix,
    )


def restart_fix(session: dict[str, Any], *, shell: bool | None = None) -> str:
    """How to get a fresh session after this one locked or went read-only.

    An agent conversation's session is keyed by the conversation, and its id never
    starts another, so only a new conversation does; a shell's or terminal's
    session is ended and the next command starts another.
    """
    session_id = session.get("id") or ""
    if shell is None:
        shell = bool(session_id) and shell_session_id() == session_id
    if session.get("agent_session") and not shell:
        return NEW_CONVERSATION_FIX
    if shell:
        return (
            "End it with `beakr session end`, then `beakr session start` and run the command "
            "again, working in one project."
        )
    return (
        f"End it with `beakr session end {session_id}` and run the command again: this terminal "
        "then starts a new session. Work in one project per session."
    )


def lock_lines(session: dict[str, Any]) -> list[str]:
    """Where the session can write now: open, locked to one project, read-only, or (read
    everything) nowhere; then what it has touched."""
    touched = ", ".join(_project_name(p) for p in session.get("touched_projects") or [])
    state = session.get("lock_state")
    locked = _project_name(session.get("write_project"))
    if reads_everything(session):
        lines = [
            "Reads every project you can read, locked ones included, and writes nothing.",
            f"To write: {restart_fix(session)}",
        ]
    elif state == "read_only":
        reason = session.get("read_only_reason") or f"Read {touched}."
        lines = [
            f"Read-only: {reason} Writes are refused; reads keep working.",
            f"To write: {restart_fix(session)}",
        ]
    elif state == "locked":
        lines = [
            f"Locked to {locked}: it can write only to {locked}. Touching another locked "
            "project makes it read-only."
        ]
    elif automatic(session):
        lines = [
            "Open: it writes where project boundaries allow. Touching a locked project "
            "locks its writes to that project."
        ]
    else:
        lines = []
    if touched:
        lines.append(f"Touched: {touched}")
    return lines


def ended_lines(session: dict[str, Any]) -> list[str]:
    """When an ended session ended (and by whom, where the API says), and what to do now."""
    at = str(session.get("ended_at") or "").replace("T", " ")[:19]
    by = session.get("ended_by")
    who = by.get("name") or by.get("email") if isinstance(by, dict) else by
    line = "Ended" + (f" at {at}" if at else "") + (f" by {who}" if who else "") + "."
    if session.get("agent_session"):
        return [
            line + " Reads and writes in it are refused, and an agent conversation's session "
            "is never started again.",
            "To work on: start a new Claude Code conversation (or Codex thread) to get a new "
            "session.",
        ]
    return [line + " Reads and writes in it are refused."]


#: Each mode in plain words, for ``Mode:`` lines.
MODE_WORDS = {
    AUTOMATIC: "automatic (the server tracks what it touches)",
    PICKED: "picked (its projects were chosen at the start)",
    READ_EVERYTHING: "read everything (every project you can read; writes nothing)",
}


def note_lines(session: dict[str, Any]) -> list[str]:
    """The API's notes on the session's lists (writes held for a project's owners)."""
    return [
        f"Note: {note['message']}"
        for note in session.get("notes") or []
        if isinstance(note, dict) and note.get("message")
    ]


def describe(session: dict[str, Any]) -> str:
    """A short human summary of a session: the API's one line, its mode, where it reads
    and writes."""

    def names(key: str) -> str:
        projects = session.get(key) or []
        return ", ".join(f"{p.get('name') or p.get('id')}" for p in projects) or "none"

    status = session.get("status", "?")
    mode = mode_of(session)
    lines = [f"Session {session.get('id')} ({status})"]
    if status == "ended":
        lines.extend(ended_lines(session))
    elif session.get("summary"):
        lines.append(str(session["summary"]))
    if mode:
        lines.append(f"Mode: {MODE_WORDS.get(mode, mode)}")
    if status == "pending":
        lines.append(f"No projects chosen yet. Choose them at {session.get('picker_url')}")
        return "\n".join(lines)
    if automatic(session) or reads_everything(session):
        if status != "ended":
            lines.extend(lock_lines(session))
        elif session.get("touched_projects"):
            lines.append(f"Touched: {names('touched_projects')}")
        if session.get("agent_session"):
            lines.append(f"Agent conversation: {session['agent_session']}")
        return "\n".join(lines)
    if status != "ended" and session.get("lock_state") in {"locked", "read_only"}:
        lines.extend(lock_lines(session))
    elif session.get("touched_projects"):
        lines.append(f"Touched: {names('touched_projects')}")
    if status == "ended":
        lines.append(f"Read: {names('read_projects')}")
        lines.append(f"Wrote: {names('write_projects')}")
    else:
        lines.append(f"Reads: {names('read_projects')}")
        lines.append(f"Writes: {names('write_projects')}")
        lines.extend(note_lines(session))
        lines.append(f"Change at {session.get('picker_url')}")
    return "\n".join(lines)


def _names_of(projects: Any) -> str:
    return ", ".join(_project_name(p) for p in projects or [] if isinstance(p, dict))


def status_lines(status: dict[str, Any], *, events: int = 5) -> list[str]:
    """Session status (``GET /v1/sessions/.../status``) in plain words: the API's summary,
    what it read, where it can still write and why not elsewhere, then its last events.

    The same lines for ``beakr session status`` and the ``session_status`` tool, worded
    as the engine's remote ``session_status`` tool words them.
    """
    lines = [str(status.get("summary") or "")]
    read = _names_of(status.get("read_projects"))
    lines.append(f"Read: {read or 'nothing yet'}.")
    write = status.get("write") if isinstance(status.get("write"), dict) else {}
    refused = [r for r in write.get("refused") or [] if isinstance(r, dict)]
    if mode_of(status) == READ_EVERYTHING:
        lines.append("Can write: nothing.")
    elif write.get("anywhere"):
        lines.append("Can write: any project you can read" + (", except:" if refused else "."))
    else:
        allowed = [
            _project_name(p) + (" (held for its owners)" if p.get("outcome") == "held" else "")
            for p in write.get("projects") or []
            if isinstance(p, dict)
        ]
        lines.append(f"Can write: {', '.join(allowed) or 'nothing'}.")
    for project in refused[:20]:
        lines.append(f"Can't write {_project_name(project)}: {project.get('reason')}")
    if len(refused) > 20:
        lines.append(f"... and {len(refused) - 20} more it can't write.")
    rows = [e for e in status.get("events") or [] if isinstance(e, dict) and e.get("summary")]
    if rows[:events]:
        lines.append("Recent:")
        lines.extend(f"- {event['summary']}" for event in rows[:events])
    return [line for line in lines if line]


#: ``beakr session status`` where no session is in use.
NOT_IN_SESSION = "This shell is not in a session: commands run as you, and no session rules apply."


def selection_reasons(source: Any) -> list[dict[str, Any]]:
    """The reasons a picked selection breaks a boundary: from ``POST /v1/sessions/options``
    or a ``422 session_selection_invalid`` refusal."""
    if isinstance(source, BeakrError):
        value = refusal_field(source, "reasons")
    else:
        value = source.get("reasons") if isinstance(source, dict) else None
    return [r for r in value or [] if isinstance(r, dict)]


def selection_invalid(source: Any, *, fix: str | None = None) -> SessionScopeError:
    """Picked lists that break a boundary: every reason, one per line, in the API's words."""
    reasons = [str(r.get("message")) for r in selection_reasons(source) if r.get("message")]
    refused = source if isinstance(source, BeakrError) else None
    message = "\n".join(reasons) or (
        refused.message if refused else "These projects can't be picked together."
    )
    return SessionScopeError(
        message,
        code=SELECTION_INVALID,
        fix=fix or (refused.fix if refused else None) or "Change the read or write list.",
    )


def narrowed(
    session: dict[str, Any],
    *,
    drop: list[str] | None = None,
    drop_write: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """The session's lists with projects dropped, matched by id or name.

    Never adds: every project named must already be in the session, and the
    result is a subset of the current lists. ``drop`` removes a project from both
    lists; ``drop_write`` keeps reading it but stops writing it.
    """
    read = session.get("read_projects") or []
    write = session.get("write_projects") or []

    def resolve(refs: list[str] | None, among: list[dict[str, Any]], kind: str) -> set[str]:
        found: set[str] = set()
        for ref in refs or []:
            ref_text = str(ref).strip()
            if not ref_text:
                continue
            matches = [
                str(p.get("id"))
                for p in among
                if ref_text == str(p.get("id"))
                or ref_text.casefold() == str(p.get("name") or "").casefold()
            ]
            if not matches:
                raise SessionScopeError(
                    f"{ref_text!r} is not one of this session's {kind} projects. A session can "
                    "only drop projects it has; adding one is the person's choice.",
                    code=WIDEN_REFUSED,
                    fix="Ask the person to add it at the session's picker link.",
                )
            found.update(matches)
        return found

    dropped = resolve(drop, read, "read")
    dropped_write = resolve(drop_write, write, "write") | (dropped & set(project_ids(write)))
    new_read = [p for p in project_ids(read) if p not in dropped]
    new_write = [p for p in project_ids(write) if p not in dropped_write and p in new_read]
    if not (set(new_read) <= set(project_ids(read)) and set(new_write) <= set(project_ids(write))):
        raise SessionScopeError("A session can only drop projects.", code=WIDEN_REFUSED)
    return new_read, new_write


# --- Folder memory ----------------------------------------------------------------------


def remembered_selection(folder: str | Path | None) -> tuple[list[str], list[str]] | None:
    """The selection remembered for ``folder`` by its binding, when it applies."""
    from beakr_cli.project_binding import binding_applies, read_binding

    if not folder:
        return None
    binding = read_binding(Path(folder))
    scope = binding.get(BINDING_FIELD) if binding else None
    if binding is None or not isinstance(scope, dict) or not binding_applies(binding):
        return None
    read = [str(p) for p in scope.get("read_project_ids") or []]
    write = [str(p) for p in scope.get("write_project_ids") or [] if str(p) in read]
    return (read, write) if read else None


def remember_selection(folder: str | Path, read: list[str], write: list[str]) -> Path:
    """Keep ``read``/``write`` in the folder's binding (creating ``.beakr/project.json``)."""
    from beakr_sdk.credentials import resolve_base_url

    from beakr_cli.project_binding import (
        BINDING_DIR,
        BINDING_FILE,
        credential_org_id,
        find_binding,
        read_binding,
    )

    folder = Path(folder)
    path = find_binding(folder)
    if path is None:
        path = folder / BINDING_DIR / BINDING_FILE
        data: dict[str, Any] = {"base_url": resolve_base_url()}
        org_id = credential_org_id()
        if org_id:
            data["org_id"] = org_id
    else:
        data = read_binding(folder) or {}
    data[BINDING_FIELD] = {"read_project_ids": list(read), "write_project_ids": list(write)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def forget_selection(folder: str | Path) -> Path | None:
    from beakr_cli.project_binding import find_binding, read_binding

    path = find_binding(Path(folder))
    if path is None:
        return None
    data = read_binding(Path(folder)) or {}
    if data.pop(BINDING_FIELD, None) is None:
        return None
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


# --- Shell sessions (the CLI) -----------------------------------------------------------


@dataclass
class TokenState:
    """A session's current token, as the shell's session file keeps it."""

    session_id: str
    token: str
    expires_at: str
    base_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def expires_soon(self, margin: float = RENEW_MARGIN_SECONDS) -> bool:
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires.timestamp() - time.time() < margin


def sessions_dir() -> Path:
    from beakr_sdk.credentials import beakr_home

    return beakr_home() / "sessions"


def _state_path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch == "-")
    return sessions_dir() / f"{safe}.json"


def save_state(state: TokenState) -> Path:
    from beakr_sdk.credentials import write_private_file

    from beakr_cli import config

    config.ensure_dir()
    directory = sessions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(directory, 0o700)
    path = _state_path(state.session_id)
    write_private_file(path, json.dumps(asdict(state)))
    return path


def load_state(session_id: str) -> TokenState | None:
    try:
        data = json.loads(_state_path(session_id).read_text())
        return TokenState(**data)
    except (OSError, ValueError, TypeError):
        return None


def delete_state(session_id: str) -> None:
    with contextlib.suppress(OSError):
        _state_path(session_id).unlink()


def _token_of(minted: dict[str, Any]) -> tuple[str, str]:
    token = minted.get("token")
    if isinstance(token, dict):  # POST /v1/sessions nests it
        return str(token["token"]), str(token["expires_at"])
    return str(minted["token"]), str(minted["expires_at"])


#: What the API takes as an agent conversation id (``agent_session``).
_AGENT_SESSION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def agent_session_id() -> str | None:
    """The agent conversation this process runs in (``X-Beakr-Session``), when one is known.

    None for a label the API would not take as an id (it keys sessions by at most
    128 of ``A-Za-z0-9._:-``): such a conversation runs as before, unshared.
    """
    from beakr_cli.sdk import _session

    agent = _session()
    return agent if agent and _AGENT_SESSION_ID.fullmatch(agent) else None


def retry_body(body: dict[str, Any], exc: BaseException) -> dict[str, Any] | None:
    """The ``POST /v1/sessions`` body to send again after ``exc``, or None to raise it.

    - ``422 feature_not_enabled`` for ``agent_session``: an installation without
      agent sessions; start the session on its own, as before.
    - ``422 session_mode_auto_lock`` for project lists: the organization locks each
      session to the first project it touches, so it takes none.
    """
    if not isinstance(exc, BeakrError) or exc.status != 422:
        return None
    if exc.code == FEATURE_NOT_ENABLED and "agent_session" in body:
        return {k: v for k, v in body.items() if k != "agent_session"}
    if exc.code == MODE_AUTO_LOCK and "read_project_ids" in body:
        return {k: v for k, v in body.items() if k not in {"read_project_ids", "write_project_ids"}}
    return None


def conversation_ended_error(agent: str) -> SessionScopeError:
    """The agent conversation's session has ended: its id never starts another one."""
    return SessionScopeError(
        f"This agent conversation's Beakr session ({agent}) has ended, and a conversation's "
        "session is never started again; nothing was sent.",
        code=SESSION_ENDED,
        fix=NEW_CONVERSATION_FIX,
    )


def conversation_ended(exc: BaseException, agent: str | None) -> SessionScopeError | None:
    """``409 session_ended`` to joining an agent conversation's session: said in its terms."""
    if not (agent and isinstance(exc, BeakrError) and exc.code == SESSION_ENDED):
        return None
    if isinstance(exc, SessionScopeError):
        return exc
    return conversation_ended_error(agent)


def session_body(
    *,
    client_name: str,
    label: str | None = None,
    folder: str | None = None,
    read: list[str] | None = None,
    write: list[str] | None = None,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
    agent: str | None = None,
    read_everything: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "client_name": client_name,
        "issue_token": True,
        "ttl_seconds": ttl_seconds,
    }
    if label:
        body["label"] = label[:200]
    if folder:
        body["folder"] = str(folder)[:1024]
    if read_everything:
        # Every project the person can read, locked ones included, and no writes.
        body["read_everything"] = True
    elif read is not None or write is not None:
        body["read_project_ids"] = list(read or [])
        body["write_project_ids"] = list(write or [])
    if agent:
        # Joins the session this agent conversation already has (beakr mcp's, or an
        # earlier command's), or starts it: one session per conversation.
        body["agent_session"] = agent
    return body


def create_session(
    sdk: Any,
    *,
    client_name: str,
    label: str | None = None,
    folder: str | None = None,
    read: list[str] | None = None,
    write: list[str] | None = None,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
    agent: str | None = None,
    read_everything: bool = False,
) -> tuple[dict[str, Any], TokenState]:
    """Start a session with the person's credential; returns it and its first token.

    With ``agent`` (an agent conversation's id) the session is that conversation's:
    joined when it exists, so ``beakr mcp`` and the commands run in the same
    conversation share it. Lists (or ``read_everything``) declare it: the API applies
    a declaration to a joined session only while it has touched nothing
    (``409 session_already_started`` otherwise).
    """
    body = session_body(
        client_name=client_name,
        label=label,
        folder=folder,
        read=read,
        write=write,
        ttl_seconds=ttl_seconds,
        agent=agent,
        read_everything=read_everything,
    )
    while True:
        try:
            session = sdk.request("POST", "/v1/sessions", json=body)
            break
        except BeakrError as exc:
            again = retry_body(body, exc)
            if again is None:
                ended = conversation_ended(exc, agent)
                if ended is not None:
                    raise ended from exc
                raise
            body = again
    token, expires_at = _token_of(session)
    return session, TokenState(
        session_id=str(session["id"]),
        token=token,
        expires_at=expires_at,
        base_url=str(getattr(sdk, "base_url", "") or ""),
    )


def renew(sdk: Any, state: TokenState, *, ttl_seconds: int = TOKEN_TTL_SECONDS) -> TokenState:
    minted = sdk.request(
        "POST", f"/v1/sessions/{state.session_id}/tokens", json={"ttl_seconds": ttl_seconds}
    )
    token, expires_at = _token_of(minted)
    return TokenState(state.session_id, token, expires_at, state.base_url, state.extra)


def shell_session_id() -> str | None:
    value = os.environ.get(SESSION_ENV, "").strip()
    return value or None


#: Set while a command runs with the person's own credential inside a session
#: (:func:`person_credential`).
_PERSON_CREDENTIAL = False
#: Set while that credential must not join any session either (``unjoined=True``).
_UNJOINED = False


@contextlib.contextmanager
def person_credential(*, unjoined: bool = False) -> Any:
    """Send the person's own credential while in a session, for a route that takes no session token.

    The CLI's write gate (:mod:`beakr_cli.write_session`) uses it for the few
    writes the API refuses to any session token, after checking itself that the
    session may write the project. ``unjoined`` also leaves out ``X-Beakr-Session``,
    so the API does not join the request to the agent conversation's session: for
    a file's approval, which only a person may give (an agent's session never
    approves its own files).
    """
    global _PERSON_CREDENTIAL, _UNJOINED
    previous = (_PERSON_CREDENTIAL, _UNJOINED)
    _PERSON_CREDENTIAL, _UNJOINED = True, unjoined or _UNJOINED
    try:
        yield
    finally:
        _PERSON_CREDENTIAL, _UNJOINED = previous


def unjoined() -> bool:
    """Whether requests now leave out ``X-Beakr-Session`` (:func:`person_credential`)."""
    return _UNJOINED


def token_session_id() -> str | None:
    """The session whose token this shell's requests carry now; None when they carry the
    person's own credential (no session, or inside :func:`person_credential`)."""
    return None if _PERSON_CREDENTIAL else shell_session_id()


def shell_token() -> str | None:
    """The token for this shell's session (``BEAKR_SCOPE_SESSION``), renewed when due.

    None when the shell has no session. Raises :class:`SessionScopeError` when it
    names one this machine has no token for, or one that has ended, rather than
    silently falling back to the person's full credential.
    """
    session_id = token_session_id()
    if session_id is None:
        # In an agent conversation whose session it runs in: its session.
        return None if _PERSON_CREDENTIAL else conversation_token()
    state = load_state(session_id)
    if state is None:
        raise SessionScopeError(
            f"{SESSION_ENV} names session {session_id}, but there is no token for it here.",
            code="session_not_found",
            fix=f"Run `beakr session start`, or `unset {SESSION_ENV}` to use your own credential.",
        )
    if state.expires_soon():
        from beakr_cli.sdk import get_sdk

        with get_sdk(scoped=False) as person:
            state = _renew_shell(person, state)
        save_state(state)
    return state.token


#: How to get out of a shell session that cannot be used any more.
LEAVE_FIX = f"Run `unset {SESSION_ENV}` to leave it, or `beakr session start` to begin a new one."


def ended(session_id: str, *, nothing_written: bool = False) -> SessionScopeError:
    """This shell's session has ended (or cannot be renewed): how to leave it or start another.

    Never the sign-in hint: the person's own credential is fine, and on a
    self-hosted installation ``beakr auth login`` has no browser sign-in anyway.
    """
    return SessionScopeError(
        f"This shell's session {session_id} has ended"
        + ("; nothing was written." if nothing_written else "."),
        code=SESSION_ENDED,
        fix=LEAVE_FIX,
    )


def _renew_shell(person: Any, state: TokenState) -> TokenState:
    """Renew the shell's token; an ended (or deleted) session is said so, not as a sign-in."""
    try:
        return renew(person, state)
    except BeakrError as exc:
        if gone(exc) or exc.code == SESSION_ENDED:
            raise ended(state.session_id) from exc
        if exc.code == "insufficient_scope":
            # Only the API key that started a session mints its tokens.
            raise SessionScopeError(
                f"This shell's session {state.session_id} has expired, and only the API key "
                "that started it can renew it.",
                code=SESSION_ENDED,
                fix=LEAVE_FIX,
            ) from exc
        raise


def explain_refusal(exc: BaseException) -> BaseException:
    """An API refusal of this shell's session, said in terms of the session.

    - ``401`` for a request that carried a session token (``bk_st_``): the session
      ended, or its token expired; never the sign-in hint the API gives a person's
      credential. When the session is still open the token is renewed for the next
      command.
    - ``403 session_write_scope``: the projects by name, not id, and how to add them.
    - ``403 session_read_only`` / ``session_locked_to_project`` (rule A): the API's
      words, with how to get a fresh session from here (:func:`restart_fix`).
    - ``403 session_required``: how to run the write in a session.

    Anything else is returned as it is.
    """
    if not isinstance(exc, BeakrError) or isinstance(exc, SessionScopeError):
        return exc
    if exc.status == 401 and sent_session_token(exc):
        session_id = shell_session_id()
        if session_id:
            return _token_refused(session_id)
        agent = agent_session_id()
        return _conversation_token_refused(agent) if agent else exc
    if exc.code == WRITE_SCOPE:
        from beakr_cli.write_session import explain_write_refusal

        return explain_write_refusal(exc, shell_session_id())
    if exc.code in {READ_ONLY, LOCKED_TO_PROJECT}:
        return explain_lock_refusal(exc)
    if exc.code == SELECTION_INVALID:
        # Each reason on its own line, in the API's words.
        return selection_invalid(exc)
    if exc.code == SESSION_REQUIRED and "inside a session" in (exc.message or ""):
        # Project isolation's refusal. The same code also means "do it in the web app"
        # (a browser session), which errors.py says as it is.
        return SessionScopeError(
            exc.message,
            code=SESSION_REQUIRED,
            fix=(
                "Run the write in a session: from your agent conversation (it joins the "
                "conversation's session), or in a shell put in one with `eval \"$(beakr session "
                'start --env)"`, then run the command again.'
            ),
        )
    if exc.code == PROJECT_REQUIRED and not isinstance(exc, SessionScopeError):
        return SessionScopeError(
            exc.message,
            code=PROJECT_REQUIRED,
            fix=(
                "Name one of the session's projects with --project (`beakr session show` "
                "lists them), and run the command once for each project you need."
            ),
        )
    return exc


def refusal_field(exc: BeakrError, name: str) -> Any:
    """A field the API's refusal carries beside its code and message (``detail.<name>``)."""
    for source in (exc.details, _detail(exc.body)):
        if isinstance(source, dict) and name in source:
            return source[name]
    return None


def explain_lock_refusal(
    exc: BeakrError, session: dict[str, Any] | None = None, *, shell: bool | None = None
) -> SessionScopeError:
    """A locked or read-only session refused a write: the API's words, and the fix that works here.

    ``session`` is the refused session when the caller holds it (the write gate);
    else it is this shell's, this agent conversation's (the command runs in one),
    or this terminal's. ``shell``: whether this shell named it
    (``BEAKR_SCOPE_SESSION``), when the caller knows better than the environment.
    """
    session_id = str(refusal_field(exc, "session_id") or (session or {}).get("id") or "")
    held = dict(session or {"id": session_id, "agent_session": agent_session_id()})
    # The API's message names the projects; the fix is only what to do.
    fix = restart_fix(held, shell=shell)
    return SessionScopeError(exc.message, code=str(exc.code), fix=fix)


def _conversation_token_refused(agent: str) -> BaseException:
    """The conversation session's token was refused: ended (final), or renewed for next time."""
    from beakr_cli.sdk import get_sdk

    key = f"agent:{agent}"
    record = _terminal_record(key) or {}
    session_id = record.get("session_id")
    state = load_state(str(session_id)) if session_id else None
    if state is None:
        return conversation_ended_error(agent)
    try:
        with get_sdk(scoped=False) as person:
            save_state(_renew_conversation(person, state, agent, key))
    except SessionScopeError as refusal:
        return refusal
    except BeakrError:
        return SessionScopeError(
            f"This agent conversation's Beakr session {session_id} has ended or its token expired.",
            code=SESSION_ENDED,
            fix=NEW_CONVERSATION_FIX,
        )
    return SessionScopeError(
        f"The token of this agent conversation's Beakr session {session_id} had expired; it "
        "has been renewed.",
        code="session_token_renewed",
        fix="Run the command again.",
    )


def gone(exc: BaseException) -> bool:
    """Whether a ``404`` says the session itself is gone (deleted, or never this person's).

    Only a refusal of the person's own credential sent without ``X-Beakr-Session``
    says so: a request joined to some agent conversation's session (or carrying a
    session token) is answered within that session, where another session is not
    found although it is open. Such a 404 is passed on as it is, never "ended".
    """
    from beakr_sdk.errors import NotFoundError

    if not isinstance(exc, NotFoundError):
        return False
    try:
        request = exc.response.request if exc.response is not None else None
    except RuntimeError:  # a response built without its request
        request = None
    if request is None:
        return True
    return "x-beakr-session" not in request.headers and not request.headers.get(
        "authorization", ""
    ).startswith(f"Bearer {SESSION_TOKEN_PREFIX}")


def sent_in_session(exc: BeakrError) -> bool:
    """Whether the refused request ran inside a session: its token, or ``X-Beakr-Session``
    (a person's key joined to an agent conversation's session)."""
    if sent_session_token(exc):
        return True
    try:
        request = exc.response.request if exc.response is not None else None
    except RuntimeError:
        return False
    return request is not None and "x-beakr-session" in request.headers


def sent_session_token(exc: BeakrError) -> bool:
    """Whether the refused request carried a session token (``bk_st_``)."""
    try:
        request = exc.response.request if exc.response is not None else None
    except RuntimeError:  # a response built without its request
        return False
    sent = request.headers.get("authorization", "") if request is not None else ""
    return sent.startswith(f"Bearer {SESSION_TOKEN_PREFIX}")


def _token_refused(session_id: str) -> SessionScopeError:
    """Why this shell's session token was refused, asked with the person's own credential."""
    from beakr_sdk.errors import NotFoundError

    from beakr_cli.sdk import get_sdk

    try:
        with get_sdk(scoped=False) as person:
            try:
                session = person.request("GET", f"/v1/sessions/{session_id}")
            except NotFoundError as missing:
                if not gone(missing):
                    raise
                return ended(session_id)
            if session.get("status") == "ended":
                return ended(session_id)
            state = load_state(session_id)
            if state is None:
                return ended(session_id)
            save_state(_renew_shell(person, state))
    except SessionScopeError as refusal:
        return refusal
    except BeakrError:
        # The person's credential could not say either; still not a sign-in problem.
        return SessionScopeError(
            f"This shell's session {session_id} has ended or its token expired.",
            code=SESSION_ENDED,
            fix=LEAVE_FIX,
        )
    return SessionScopeError(
        f"The token of this shell's session {session_id} had expired; it has been renewed.",
        code="session_token_renewed",
        fix="Run the command again.",
    )


def keep_remembered(session: dict[str, Any]) -> None:
    """Keep the selection in the folder's binding when the person asked for it on the picker."""
    folder = session.get("folder")
    if not folder or not session.get("remember_folder") or session.get("status") != "active":
        return
    if not Path(folder).is_dir():
        return
    remember_selection(
        folder,
        project_ids(session.get("read_projects")),
        project_ids(session.get("write_projects")),
    )


# --- This terminal's session ------------------------------------------------------------
#
# A write the CLI makes outside a named session (``BEAKR_SCOPE_SESSION``) opens one
# and keeps it for the terminal it ran in, so the next write there reuses it
# instead of asking again (:mod:`beakr_cli.write_session`). "The terminal" is the
# agent session when the command runs under one (Claude Code, Codex, Cursor: their
# own session id), else the controlling terminal and its process session (the
# shell the terminal tab started), else the process session alone. Only the
# session's id is kept, under ``~/.beakr/sessions/terminals``; its token stays in
# the session's own file.


def terminal_key() -> str:
    """What identifies the terminal (or agent session) this command runs in."""
    from beakr_cli.sdk import _session as agent_session

    agent = agent_session()
    if agent:
        return f"agent:{agent}"
    getsid = getattr(os, "getsid", None)
    leader = getsid(0) if getsid is not None else os.getppid()
    for fd in (0, 1, 2):
        try:
            return f"tty:{os.ttyname(fd)}:{leader}"
        except OSError:
            continue
    return f"process:{leader}"


def _terminal_path(key: str) -> Path:
    import hashlib

    return sessions_dir() / "terminals" / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"


def terminal_session_id(key: str | None = None) -> str | None:
    """The session this terminal kept, when its token is still here."""
    try:
        data = json.loads(_terminal_path(key or terminal_key()).read_text())
    except (OSError, ValueError):
        return None
    session_id = data.get("session_id") if isinstance(data, dict) else None
    if not isinstance(session_id, str) or load_state(session_id) is None:
        return None
    return session_id


def terminal_recorded_id(key: str | None = None) -> str | None:
    """The session this terminal kept last, even once ended (its token gone)."""
    record = _terminal_record(key or terminal_key())
    session_id = record.get("session_id") if record else None
    return session_id if isinstance(session_id, str) and session_id else None


def remember_terminal(
    session_id: str | None,
    key: str | None = None,
    *,
    mode: str | None = None,
    declared: bool = False,
) -> Path:
    """Keep the terminal's session (``None``: none, with the organization's ``mode`` known).

    ``declared``: an agent conversation chose this session's projects itself
    (``beakr session start`` inside it), so its commands run in it.
    """
    from beakr_sdk.credentials import write_private_file

    key = key or terminal_key()
    path = _terminal_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    record: dict[str, Any] = {"session_id": session_id, "terminal": key}
    if mode:
        record["mode"] = mode
    if declared:
        record["declared"] = True
    write_private_file(path, json.dumps(record))
    return path


def _terminal_record(key: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_terminal_path(key).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- An agent conversation's session (project boundaries) ---------------------------------
#
# Where the organization has project boundaries on and sessions start automatic
# (``session_mode: automatic``), every command an agent runs in a conversation runs
# in that conversation's session, reads included: ``beakr mcp``, its subagents and
# the CLI calls in the same conversation share it (``agent_session``), so none can
# touch a locked project, or write elsewhere after touching it, outside the lock.
# The same holds once the conversation declared its session (``beakr session start``
# or ``start_session`` in it), in any organization. A person's API key sending
# ``X-Beakr-Session`` joins it on the server anyway; this covers every credential,
# and is checked once per conversation (the answer is kept with the terminal's
# session).

#: Session modes an agent conversation's commands run in without declaring them.
JOINED_MODES = frozenset({AUTOMATIC, AUTO_LOCK, READ_EVERYTHING})

#: Off only in the test suite (tests/conftest.py), like ``write_session.ENABLED``.
CONVERSATION_SESSIONS = True


def isolation_path(org_id: str) -> str:
    """Where the API keeps the organization's project isolation settings."""
    return f"/v1/organizations/{org_id}/project-isolation"


def _joined(record: dict[str, Any]) -> bool:
    """Whether a conversation's kept record says its commands run in its session."""
    return bool(record.get("declared")) or record.get("mode") in JOINED_MODES


def conversation_token() -> str | None:
    """The token of this agent conversation's session; None outside one.

    None when the command does not run in an agent conversation, when the
    credential holds no session (a service account, a session token), or when the
    conversation neither declared a session nor runs where sessions start automatic
    (an organization that makes each session pick, or without project boundaries:
    reads stay as before).
    """
    agent = agent_session_id()
    if not agent or not CONVERSATION_SESSIONS:
        return None
    key = f"agent:{agent}"
    record = _terminal_record(key)
    if record is not None and not _joined(record):
        return None
    kept = (record or {}).get("session_id")
    state = load_state(str(kept)) if kept else None
    if state is None:
        state = _join_conversation(agent, key)
        if state is None:
            return None
    if state.expires_soon():
        from beakr_cli.sdk import get_sdk

        with get_sdk(scoped=False) as person:
            state = _renew_conversation(person, state, agent, key)
        save_state(state)
    return state.token


def conversation_state(agent: str) -> TokenState:
    """A token for this agent conversation's own session, joined (or started) when this
    terminal holds none. For ``beakr session`` inside an agent conversation, whose reads
    of sessions run with it, so the API answers for this conversation's session only.

    Raises :class:`SessionScopeError`: ``session_ended`` when the conversation's
    session has ended (never started again), ``no_conversation_sessions`` where the
    installation keeps no session per conversation.
    """
    from beakr_cli.sdk import get_sdk

    key = f"agent:{agent}"
    kept = (_terminal_record(key) or {}).get("session_id")
    state = load_state(str(kept)) if kept else None
    if state is not None:
        if state.expires_soon():
            with get_sdk(scoped=False) as person:
                state = _renew_conversation(person, state, agent, key)
            save_state(state)
        return state
    folder = os.getcwd()
    body = session_body(
        client_name="beakr-cli", label=f"beakr in {Path(folder).name}", folder=folder, agent=agent
    )
    with get_sdk(scoped=False) as person:
        try:
            session = person.request("POST", "/v1/sessions", json=body)
        except BeakrError as exc:
            ended = conversation_ended(exc, agent)
            if ended is not None:
                raise ended from exc
            if exc.code == FEATURE_NOT_ENABLED:
                raise SessionScopeError(
                    "This installation keeps no session per agent conversation, so "
                    "`beakr session` cannot read sessions from inside one.",
                    code="no_conversation_sessions",
                    fix=OUTSIDE_CONVERSATION_FIX,
                ) from exc
            raise
    token, expires_at = _token_of(session)
    state = TokenState(str(session["id"]), token, expires_at)
    save_state(state)
    remember_terminal(state.session_id, key, mode=str(session.get("mode") or PICKED))
    return state


#: Where a person reads or ends sessions other than an agent conversation's own.
OUTSIDE_CONVERSATION_FIX = (
    "Use the web app (Settings, Sessions) or a terminal outside the agent conversation. "
    "Commands typed with `!` in Claude Code share the agent's environment, so they are "
    "confined the same way."
)


#: The refusal of a person's own act from inside an agent conversation.
OUTSIDE_CONVERSATION = "session_outside_conversation"


def refuse_inside_conversation(what: str, where: str) -> None:
    """Refuse a person's own act (it could widen the agent's reach) inside an agent
    conversation: starting or changing a session, loosening a setting, approving a file.

    "Inside" is what the environment says (an agent conversation id: Claude Code,
    Codex, Cursor or ``BEAKR_SESSION``), so ``!`` commands typed in Claude Code are
    refused too. An agent that clears those variables is not stopped here: the lock
    guards against mistakes, not a determined agent.
    """
    agent = agent_session_id()
    if not agent:
        return
    raise SessionScopeError(
        f"`{what}` is refused inside an agent conversation ({agent}): it acts with your own "
        "key and could widen what the agent reaches.",
        code=OUTSIDE_CONVERSATION,
        fix=(
            f"{where} in the web app or a terminal outside the agent conversation. Commands "
            "typed with `!` in Claude Code share the agent's environment, so they are refused "
            "the same way."
        ),
    )


def _join_conversation(agent: str, key: str) -> TokenState | None:
    """Join (or start) the conversation's session where sessions start automatic."""
    from beakr_cli.sdk import get_sdk
    from beakr_cli.write_session import _exempt_credential

    if _exempt_credential():
        return None
    from beakr_cli.project_binding import credential_org_id

    with get_sdk(scoped=False) as person:
        org_id = credential_org_id()
        try:
            settings = person.request("GET", isolation_path(org_id)) if org_id else {}
        except BeakrError:
            settings = {}  # an installation without project isolation: as before
        if not sessions_start_automatic(settings):
            remember_terminal(None, key, mode=str(settings.get("session_mode") or PICKED))
            return None
        folder = os.getcwd()
        session, state = create_session(
            person,
            client_name="beakr-cli",
            label=f"beakr in {Path(folder).name}",
            folder=folder,
            agent=agent,
        )
    mode = str(session.get("mode") or PICKED)
    save_state(state)
    remember_terminal(state.session_id, key, mode=mode)
    # A session started before the organization switched keeps the mode it began with.
    return state if mode in JOINED_MODES else None


def sessions_start_automatic(settings: Any) -> bool:
    """Whether the organization's settings start every agent session automatic, tracked
    by the server: agent sessions and project boundaries on, ``session_mode: automatic``.

    An engine before project boundaries said so with ``session_auto_lock`` and
    ``session_mode: auto_lock``.
    """
    if not isinstance(settings, dict):
        return False
    available = settings.get("available")
    if not (isinstance(available, dict) and available.get("server_agent_sessions")):
        return False
    if available.get("project_boundaries") and settings.get("session_mode") == AUTOMATIC:
        return True
    return bool(available.get("session_auto_lock")) and settings.get("session_mode") == AUTO_LOCK


def declare_conversation(
    agent: str,
    *,
    read: list[str] | None = None,
    write: list[str] | None = None,
    read_everything: bool = False,
) -> dict[str, Any]:
    """Declare this agent conversation's session (``POST /v1/sessions`` with
    ``agent_session``), as the ``start_session`` tool does, with the person's own credential.

    The API applies the lists (or ``read_everything``) only while the session has
    touched nothing (``409 session_already_started`` otherwise), so an agent picks
    up front and never widens later. Nothing declared: the session as it starts
    (automatic, or pending where every session must pick). Its token is kept, and
    the conversation's commands run in it from now on.
    """
    from beakr_cli.sdk import get_sdk

    folder = os.getcwd()
    with get_sdk(scoped=False) as person:
        session, state = create_session(
            person,
            client_name="beakr-cli",
            label=f"beakr in {Path(folder).name}",
            folder=folder,
            read=read,
            write=write,
            agent=agent,
            read_everything=read_everything,
        )
    save_state(state)
    declared = read_everything or read is not None or write is not None
    remember_terminal(
        state.session_id,
        f"agent:{agent}",
        mode=str(session.get("mode") or PICKED),
        declared=declared,
    )
    return session


def _renew_conversation(person: Any, state: TokenState, agent: str, key: str) -> TokenState:
    """Renew the conversation session's token; an ended one is final for the conversation."""
    try:
        return renew(person, state)
    except BeakrError as exc:
        if gone(exc) or exc.code == SESSION_ENDED:
            forget_terminal(key)
            delete_state(state.session_id)
            raise conversation_ended_error(agent) from exc
        raise


def forget_terminal(key: str | None = None) -> None:
    with contextlib.suppress(OSError):
        _terminal_path(key or terminal_key()).unlink()


# --- The MCP server's session -----------------------------------------------------------


class McpSession:
    """The session ``beakr mcp`` holds for its lifetime."""

    def __init__(self, folder: str | None = None, agent: str | None = None) -> None:
        self.folder = folder
        #: The agent conversation the server runs in (``X-Beakr-Session``): its session
        #: is the conversation's, shared with the commands the agent runs.
        self.agent = agent
        self.state: TokenState | None = None
        self.session: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._labelled = False
        self._remembered = False
        #: Why the session could not be started yet; the next tool call retries.
        self.start_error: str | None = None
        #: The ended session this one replaced, until the picker has said so.
        self.replaced_from: str | None = None
        #: The agent conversation whose session the person ended: it is not replaced.
        self.ended_conversation: str | None = None
        #: That session's id, for ``session_status`` to show it.
        self.ended_session_id: str | None = None
        #: Start with the folder's remembered projects. Off once the person ended a
        #: session: its replacement reaches nothing until they choose again.
        self.reuse_remembered = True

    # The person's own credential: starting, renewing, choosing (elicitation), ending.
    @staticmethod
    def _person() -> Any:
        from beakr_cli.sdk import MCP_CLIENT_NAME, get_async_sdk

        return get_async_sdk(client_name=MCP_CLIENT_NAME, scoped=False, timeout=30.0)

    def _scoped(self) -> Any:
        from beakr_cli.sdk import MCP_CLIENT_NAME, get_async_sdk

        return get_async_sdk(client_name=MCP_CLIENT_NAME, timeout=30.0)

    @property
    def id(self) -> str | None:
        return self.state.session_id if self.state else None

    @property
    def token(self) -> str | None:
        return self.state.token if self.state else None

    @property
    def pending(self) -> bool:
        return self.session.get("status") == "pending"

    @property
    def picker_url(self) -> str | None:
        return self.session.get("picker_url")

    async def start(self) -> bool:
        """Start the session; False (no session, as before) where the API has none."""
        from beakr_sdk.errors import NotFoundError

        remembered = remembered_selection(self.folder) if self.reuse_remembered else None
        body = session_body(
            client_name="beakr mcp",
            label=_label(None, self.folder),
            folder=self.folder,
            read=remembered[0] if remembered else None,
            write=remembered[1] if remembered else None,
            agent=self.agent,
        )
        async with self._person() as person:
            while True:
                try:
                    session = await person.request("POST", "/v1/sessions", json=body)
                    break
                except NotFoundError:
                    return False
                except BeakrError as exc:
                    again = retry_body(body, exc)
                    if again is None:
                        ended = conversation_ended(exc, self.agent)
                        if ended is not None:
                            raise ended from exc
                        raise
                    body = again
        token, expires_at = _token_of(session)
        self.state = TokenState(str(session["id"]), token, expires_at)
        self.session = session
        return True

    async def ensure_started(self) -> None:
        """Start the session now if starting it failed before; raise when it still fails."""
        if self.state is not None:
            return
        if self.ended_conversation:
            # The person ended this conversation's session: it is never started again.
            raise conversation_ended_error(self.ended_conversation)
        async with self._lock:
            if self.state is not None:
                return
            try:
                started = await self.start()
            except SessionScopeError as exc:
                self.start_error = str(exc)
                raise
            except Exception as exc:  # noqa: BLE001 - reported to the agent
                self.start_error = str(exc)
                raise SessionScopeError(
                    f"Beakr could not start this session: {exc}",
                    code="session_unavailable",
                    fix="Retry shortly. Beakr's tools stay unavailable until the session starts.",
                ) from exc
            if not started:
                raise SessionScopeError(
                    "This Beakr installation no longer offers sessions.",
                    code="session_unavailable",
                    fix="Restart the Beakr MCP server.",
                )
            self.start_error = None

    async def fresh_token(self) -> str | None:
        """The token, renewed with the person's credential when it is about to expire."""
        if self.state is None:
            return None
        if self.state.expires_soon():
            async with self._lock:
                if self.state.expires_soon():
                    async with self._person() as person:
                        minted = await person.request(
                            "POST",
                            f"/v1/sessions/{self.state.session_id}/tokens",
                            json={"ttl_seconds": TOKEN_TTL_SECONDS},
                        )
                    token, expires_at = _token_of(minted)
                    self.state = TokenState(self.state.session_id, token, expires_at)
        return self.state.token

    async def refresh(self) -> dict[str, Any]:
        """Read the session again (with its own token): status, lists, remember_folder.

        A refused token is recovered (:meth:`recover`): renewed and read again, or,
        when the person ended the session, its pending replacement is the answer.
        """
        try:
            self.session = await self._current()
        except BeakrError as exc:
            outcome = await self.recover(exc)
            if outcome is None:
                raise
            if outcome == RENEWED:
                try:
                    self.session = await self._current()
                except BeakrError as again:
                    if again.status == 401 and sent_session_token(again):
                        raise self.token_refused() from again
                    raise
        self._keep_if_remembered()
        return self.session

    async def _current(self) -> dict[str, Any]:
        await self.fresh_token()
        async with self._scoped() as scoped:
            return await scoped.request("GET", "/v1/sessions/current")

    async def recover(self, exc: BaseException) -> str | None:
        """After the API refused this session's token (``401``): find out why, with the
        person's own credential, and act.

        - The session is still open (its token alone expired): a new token is
          minted, and :data:`RENEWED` tells the caller to retry once.
        - The person ended it (or it is gone): a new session is started without
          projects (pending), so it reaches nothing until the person chooses, and
          :data:`REPLACED` is returned; the next picker says why it is asking. An
          agent conversation's session (``agent_session``) is not replaced: its
          id never starts another, so :class:`SessionScopeError` says to start a new
          conversation.

        None for any other refusal. Never the sign-in hint: a refused session token
        says nothing about the person's own credential.
        """
        from beakr_sdk.errors import NotFoundError

        if not (
            isinstance(exc, BeakrError)
            and exc.status == 401
            and sent_session_token(exc)
            and self.state is not None
        ):
            return None
        refused = self.state
        async with self._lock:
            if self.state is not refused:
                # Another tool call recovered first.
                if self.state is None and self.ended_conversation:
                    raise conversation_ended_error(self.ended_conversation)
                if self.state is None or self.state.session_id != refused.session_id:
                    return REPLACED
                return RENEWED
            try:
                async with self._person() as person:
                    try:
                        current: dict[str, Any] | None = await person.request(
                            "GET", f"/v1/sessions/{refused.session_id}"
                        )
                    except NotFoundError as missing:
                        if not gone(missing):
                            raise
                        current = None
                    if current is not None and current.get("status") != "ended":
                        try:
                            minted = await person.request(
                                "POST",
                                f"/v1/sessions/{refused.session_id}/tokens",
                                json={"ttl_seconds": TOKEN_TTL_SECONDS},
                            )
                        except BeakrError as renewal:
                            if not (gone(renewal) or renewal.code == SESSION_ENDED):
                                raise
                            # Ended between the two requests: replaced below.
                        else:
                            token, expires_at = _token_of(minted)
                            self.state = TokenState(refused.session_id, token, expires_at)
                            return RENEWED
            except BeakrError as check:
                raise SessionScopeError(
                    "Beakr refused this session's token, and checking the session with your "
                    f"own credential failed too: {check.message}",
                    code=SESSION_ENDED,
                    fix=check.fix
                    or "Retry shortly; if it keeps failing, restart the Beakr MCP server.",
                ) from check
            conversation = self.session.get("agent_session")
            if conversation:
                # An agent conversation's session is never started again (the API
                # refuses its id with 409 session_ended): a fresh session would let
                # the agent write with another project's content still in context.
                self.state = None
                self.session = {}
                self.ended_conversation = str(conversation)
                self.ended_session_id = refused.session_id
                raise conversation_ended_error(self.ended_conversation)
            await self._replace(refused.session_id)
            return REPLACED

    async def _replace(self, ended_id: str) -> None:
        """Start a pending session in place of ``ended_id``, which the person ended."""
        self.state = None
        self.session = {}
        self._labelled = False
        self._remembered = False
        self.reuse_remembered = False
        self.replaced_from = ended_id
        try:
            started = await self.start()
        except Exception as exc:  # noqa: BLE001 - reported to the agent
            # ensure_started retries on the next tool call; never the person's reach.
            self.start_error = str(exc)
            raise SessionScopeError(
                f"{REPLACED_MESSAGE} Beakr could not start the new session yet: {exc}",
                code=SESSION_ENDED,
                fix="Retry shortly. Beakr's tools stay unavailable until the session starts.",
            ) from exc
        if not started:
            raise SessionScopeError(
                "This Beakr installation no longer offers sessions.",
                code="session_unavailable",
                fix="Restart the Beakr MCP server.",
            )

    def take_replaced(self) -> bool:
        """True once after the session was replaced, for the picker to say why it asks."""
        replaced, self.replaced_from = self.replaced_from is not None, None
        return replaced

    def token_refused(self) -> SessionScopeError:
        """The session's token was refused again after :meth:`recover`."""
        if self.replaced_from is not None:
            return SessionScopeError(
                REPLACED_MESSAGE,
                code=NEEDS_SELECTION,
                fix=f"Show the user this link and ask them to choose the projects, then retry: "
                f"{self.picker_url or ''}",
            )
        return SessionScopeError(
            "Beakr refused this session's token even after renewing it.",
            code=SESSION_ENDED,
            fix="Retry; if it keeps failing, restart the Beakr MCP server.",
        )

    async def ended_record(self) -> dict[str, Any] | None:
        """The conversation's ended session, read with the person's own credential.

        By the id this server held, else (it started after the session ended: the API
        refused to start another, ``409 session_ended``) among the person's sessions
        by the conversation's id. None when neither finds it.
        """
        async with self._person() as person:
            if self.ended_session_id:
                return await person.request("GET", f"/v1/sessions/{self.ended_session_id}")
            if not self.agent:
                return None
            found = None
            async for row in person.paginate("/v1/sessions", include_ended=True):
                if row.get("agent_session") == self.agent:
                    found = row
                    break
            return found

    async def ended_trail(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """A trail of the ended session, read with the person's own credential."""
        async with self._person() as person:
            return await person.request("GET", path, params=params)

    async def name_client(self, agent: str | None) -> None:
        """Label the session with the MCP client once it is known (a label is no widening)."""
        if self._labelled or not agent or self.state is None:
            return
        self._labelled = True
        with contextlib.suppress(Exception):
            await self.fresh_token()
            async with self._scoped() as scoped:
                self.session = await scoped.request(
                    "PATCH", f"/v1/sessions/{self.id}", json={"label": _label(agent, self.folder)}
                )

    async def narrow(
        self, *, drop: list[str] | None = None, drop_write: list[str] | None = None
    ) -> dict[str, Any]:
        """Drop projects with the session's own token; never adds one (here, and by the API)."""
        current = await self.refresh()
        read, write = narrowed(current, drop=drop, drop_write=drop_write)
        async with self._scoped() as scoped:
            self.session = await scoped.request(
                "PATCH",
                f"/v1/sessions/{self.id}",
                json={"read_project_ids": read, "write_project_ids": write},
            )
        return self.session

    async def declare(
        self,
        *,
        read: list[str] | None = None,
        write: list[str] | None = None,
        read_everything: bool = False,
    ) -> dict[str, Any]:
        """``start_session``: choose this session's projects up front, with the person's
        own credential, as the engine's remote tool does.

        An agent conversation's session is declared through ``POST /v1/sessions`` with
        ``agent_session``, which the API applies only while it has touched nothing
        (``409 session_already_started``). Without a conversation id the server's own
        session is changed the same way, checked here first.
        """
        if self.agent:
            body = session_body(
                client_name="beakr mcp",
                label=_label(None, self.folder),
                folder=self.folder,
                read=read,
                write=write,
                agent=self.agent,
                read_everything=read_everything,
            )
            async with self._person() as person:
                try:
                    session = await person.request("POST", "/v1/sessions", json=body)
                except BeakrError as exc:
                    ended = conversation_ended(exc, self.agent)
                    if ended is not None:
                        raise ended from exc
                    raise
            token, expires_at = _token_of(session)
            self.state = TokenState(str(session["id"]), token, expires_at)
            self.session = session
            return session
        current = await self.refresh()
        touched = current.get("touched_projects") or []
        if touched and (read is not None or write is not None or read_everything):
            names = ", ".join(_project_name(p) for p in touched)
            raise SessionScopeError(
                f"This session has already worked in {names}, so its projects can't be chosen now.",
                code=ALREADY_STARTED,
                fix="Start a new session (restart the Beakr MCP server) to choose its projects.",
            )
        patch: dict[str, Any] = {}
        if read_everything:
            patch["read_everything"] = True
        elif read is not None or write is not None:
            patch = {"read_project_ids": list(read or []), "write_project_ids": list(write or [])}
        if not patch:
            return current
        async with self._person() as person:
            self.session = await person.request("PATCH", f"/v1/sessions/{self.id}", json=patch)
        return self.session

    async def choose(self, read: list[str], write: list[str], *, remember: bool) -> dict[str, Any]:
        """The person's choice from the in-chat form, applied with their own credential."""
        body: dict[str, Any] = {"read_project_ids": read, "write_project_ids": write}
        if remember and self.folder:
            body["remember_folder"] = True
        async with self._person() as person:
            self.session = await person.request("PATCH", f"/v1/sessions/{self.id}", json=body)
        self._keep_if_remembered()
        return self.session

    async def person_projects(self, limit: int = MAX_PROJECTS + 1) -> list[dict[str, Any]]:
        """The projects the person can see, at most ``limit`` (one more than a session
        holds, so a caller can tell when there are too many)."""
        projects: list[dict[str, Any]] = []
        async with self._person() as person:
            async for project in person.paginate("/v1/projects", page_size=100):
                projects.append(project)
                if len(projects) >= limit:
                    break
        return projects

    async def end(self) -> None:
        if self.state is None:
            return
        with contextlib.suppress(Exception):
            async with self._person() as person:
                await person.request("POST", f"/v1/sessions/{self.id}/end")
        self.state = None

    def _keep_if_remembered(self) -> None:
        """Save the selection to the folder's binding once the person asks for it."""
        if self._remembered or not self.folder or not self.session.get("remember_folder"):
            return
        if self.session.get("status") != "active":
            return
        self._remembered = True
        with contextlib.suppress(OSError):
            remember_selection(
                self.folder,
                project_ids(self.session.get("read_projects")),
                project_ids(self.session.get("write_projects")),
            )


def _label(agent: str | None, folder: str | None) -> str:
    where = Path(folder).name if folder else ""
    who = agent or "beakr mcp"
    return f"{who} in {where}"[:200] if where else who[:200]


#: The session this ``beakr mcp`` process holds, once started.
ACTIVE: McpSession | None = None


def active_token() -> str | None:
    return ACTIVE.token if ACTIVE is not None else None


async def start_for_mcp() -> McpSession | None:
    """Start the server's session unless ``BEAKR_MCP_SESSIONS=off``; None without one.

    Only an installation without sessions (``404``) runs as before, with the
    person's credential. Any other failure (offline, a server error) leaves the
    session to be started by the next tool call (:meth:`McpSession.ensure_started`),
    so a transient error never hands the agent the person's whole reach.
    """
    global ACTIVE
    if os.environ.get(MCP_SESSIONS_ENV, "").strip().lower() in {"off", "0", "false", "no"}:
        return None
    session = McpSession(folder=os.getcwd(), agent=agent_session_id())
    try:
        if not await session.start():
            return None
    except Exception as exc:  # noqa: BLE001 - retried by the next tool call
        if _no_credential(exc):
            return None
        session.start_error = str(exc)
    ACTIVE = session
    return session


def _no_credential(exc: Exception) -> bool:
    """No sign-in at all: the tools fail the same way with or without a session."""
    from beakr_sdk.errors import AuthenticationError

    return isinstance(exc, AuthenticationError) and getattr(exc, "response", None) is None


async def end_for_mcp() -> None:
    global ACTIVE
    session, ACTIVE = ACTIVE, None
    if session is not None:
        await session.end()
