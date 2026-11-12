"""Writes into a project run in a session: the person chooses which projects a terminal may write.

A guardrail against mistakes (filing one partner project's knowledge in another),
not a security boundary. Every command that writes into a project's content
(:data:`WRITE_COMMANDS`) runs inside a session project scope
(:mod:`beakr_cli.scope_session`), the way ``beakr mcp`` does. Reads stay
org-wide and never ask.

Which session:

1. ``BEAKR_SCOPE_SESSION`` (``beakr session start``) names one: it is used.
2. Else the session this terminal kept (:func:`scope_session.terminal_key`: the
   agent session the command runs under, else the terminal tab), while it is open.
3. Else one is started. At a terminal, the checkbox picker of ``beakr session
   start`` opens (pre-ticked from the folder's remembered selection, else the
   project the command writes); not at a terminal (an agent running the CLI),
   the session starts pending and the command prints the picker link and waits,
   up to :data:`WAIT_SECONDS`, for the person to choose. Either way the terminal
   keeps the session, so its next write does not ask again.

How the write runs (:data:`WRITE_COMMANDS`):

- ``token``: with the session's token. The API confines it to the session's
  write list and refuses any other project (``403 session_write_scope``).
- ``checked``: the API takes no session token on a route the command needs
  (conversation imports and compounding, an interview's Knowledge, the profile
  agent's runs). The CLI checks the project it writes is
  in the session's write list, then sends the person's own credential.
- ``person``: only a person may do it (a file's approval: the API refuses a
  session token with ``403 person_review_required``, so an agent's session never
  approves its own files). Checked like ``checked``, then sent with the person's
  own credential and without ``X-Beakr-Session``, so it is joined to no session.

No session is asked for where none can be held: an installation without sessions
(``404`` on ``POST /v1/sessions``, as ``beakr mcp`` treats it), a service account
or other non-person credential (the API refuses it a session), or a credential
that is itself a session token. A hook (``beakr compound --hook``) never asks or
waits (:func:`_run_hook`).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from beakr_sdk.errors import BeakrError, NotFoundError, PermissionDeniedError

from beakr_cli import scope_session
from beakr_cli.scope_session import SessionScopeError

T = TypeVar("T")

TOKEN = "token"
CHECKED = "checked"
PERSON = "person"

#: Published commands that write into a project's content, and how each runs in
#: the session (module docstring). ``tests/test_cli_write_sessions.py`` checks
#: every published command is here or in :data:`NOT_GATED`.
WRITE_COMMANDS: dict[str, str] = {
    # Files
    "upload": TOKEN,
    "files upload": TOKEN,
    "files rm": TOKEN,
    # Only a person approves a file: their own key, joined to no session.
    "files approve": PERSON,
    # A project's Knowledge and its profile
    "projects retry": TOKEN,
    "projects profile set": TOKEN,
    "projects profile edit": TOKEN,
    "projects profile setup": CHECKED,
    "kb add-fact": TOKEN,
    "kb add-connection": TOKEN,
    "kb add-finding": TOKEN,
    "kb edit-rows": TOKEN,
    "kb propose create": TOKEN,
    "kb propose edit": TOKEN,
    "kb propose replace": TOKEN,
    "kb propose move": TOKEN,
    "kb propose archive": TOKEN,
    "kb proposals accept": TOKEN,
    "kb proposals dismiss": TOKEN,
    "interviews link send": CHECKED,
    # Conversations, which compounding reads into a project's Knowledge
    "conversations import": CHECKED,
    "compound": CHECKED,
}

#: Writes that use a session already open here (named, or kept for this terminal or
#: agent conversation) but never start one: the session would only serve the CLI's
#: own check (they run ``checked``), and each run left an empty session behind
#: (``projects profile setup`` is a conversation of separate one-turn runs when not
#: at a terminal: rc.9 QA found two empty sessions after one setup).
NO_NEW_SESSION: frozenset[str] = frozenset(
    {
        "projects profile setup",
        # A person's review from a plain terminal is sent with their own key and no
        # session (commands/kb.py ``_accept_bound``): a new session would serve nothing.
        "kb proposals accept",
    }
)

#: Published commands that run as before: they read, act on the organization or
#: its administration, on the person's own account, library or machine, or on
#: the session itself.
NOT_GATED: frozenset[str] = frozenset(
    {
        # Reads
        "whoami", "context", "ask", "research", "doctor", "version",
        "kb ls", "kb cat", "kb sections", "kb search", "kb grep", "kb log", "kb sources",
        "kb research", "kb rows", "kb query", "kb entities", "kb proposals list",
        "kb proposals ls", "kb proposals show", "kb agent-review show",
        "workspace projects", "projects ls", "projects show", "projects status",
        "projects refusals", "projects profile show", "projects members ls",
        "members ls", "org show", "org service-accounts ls", "files ls", "files show",
        "files download", "documents search", "skills ls", "skills show", "skills download",
        "skills proposals", "conversations ls", "conversations show", "conversations search",
        "hooks status", "integrations ls", "integrations status", "integrations tools",
        "runs ls", "runs show", "interviews link ls", "interviews link show",
        "end-user-tokens ls", "external-users ls", "external-users show", "labels ls",
        "labels show", "labels members ls", "labels projects ls", "jobs show", "jobs wait",
        # Organization and project administration (who may do what, not content)
        "projects create", "projects archive", "projects rm", "projects members add",
        "projects members rm", "projects members invite", "members role", "members rm",
        "org service-accounts create", "org service-accounts disable",
        "org service-accounts key", "kb agent-review set", "labels create", "labels edit",
        "labels rm", "labels members add", "labels members rm", "labels projects add",
        "labels projects rm", "external-users create", "external-users edit",
        "external-users rm", "end-user-tokens mint", "end-user-tokens revoke",
        "integrations connect", "integrations disconnect", "integrations rm",
        # Project isolation settings: the person's own credential, joined to no session
        "org isolation show", "org isolation set", "org read-trail show", "org read-trail set",
        "projects guard show", "projects guard set",
        # Sign-in and credentials
        "auth login", "auth logout", "auth status", "auth whoami", "auth token", "auth dev",
        "auth set-org", "auth clear-org", "auth set-scope", "keys ls", "keys create",
        "keys revoke", "keys scopes",
        # The person's own skill library (skills belong to no project)
        "skills add", "skills install", "skills uninstall", "skills rm", "skills edit",
        "skills sync", "skills approve", "skills dismiss",
        # The person's private conversations: removing one, stopping its compounding
        "conversations rm", "conversations compound cancel",
        # An interview link belongs to no project until it is sent (`interviews link send`)
        "interviews link create", "interviews link revoke",
        # Calls a connected service's tool; writes nothing into Beakr
        "integrations call",
        # Cancels a run
        "runs cancel",
        # This machine: binding a folder, installing, hooks settings, the MCP server
        # (which holds its own session), the Codex wrapper
        "init", "install", "setup", "update", "mcp", "codex", "hooks install",
        "hooks uninstall", "hooks capture", "hooks learning",
        # The sessions themselves (always the person's own credential)
        "session start", "session show", "session trail", "session ls", "session edit",
        "session end",
        "session forget",
    }
)  # fmt: skip

#: ``beakr api`` operations the API refuses to any session token (no single
#: project in their path, and not confined in the route): run ``checked``.
_TOKEN_REFUSED_OPS = frozenset(
    {
        "POST /v1/conversations/import",
        "POST /v1/compound",
    }
)
#: ``beakr api`` operations that write a project's content under another scope: run
#: with the session's token (a file's Knowledge is confined to the session's
#: projects by the route).
_TOKEN_OPS = frozenset(
    {
        "POST /v1/artifacts/{artifact_id}/knowledge",
        "POST /v1/interview/sendables/links/{link_id}/knowledge",
    }
)
#: ``beakr api`` operations only a person may make: run ``person``.
_PERSON_OPS = frozenset({"POST /v1/artifacts/{artifact_id}/knowledge/approve"})
#: Operation scopes whose writes are a project's content.
_CONTENT_SCOPES = frozenset({"kb:write", "uploads:write"})

#: How long a command not at a terminal waits for the person to choose, and how often it looks.
WAIT_SECONDS = 300.0
POLL_SECONDS = 3.0

#: Off only in the test suite, for tests of other commands (tests/conftest.py).
ENABLED = True

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


@dataclass
class Held:
    """The session a write runs in."""

    session: dict[str, Any]
    #: Chosen while this command ran, rather than named or kept before.
    new: bool = False

    @property
    def id(self) -> str:
        return str(self.session["id"])

    @property
    def picker_url(self) -> str:
        return str(self.session.get("picker_url") or "")

    @property
    def active(self) -> bool:
        return self.session.get("status") == "active"

    @property
    def auto_locked(self) -> bool:
        return scope_session.auto_locked(self.session)

    def writes(self) -> list[str]:
        return scope_session.project_ids(self.session.get("write_projects"))

    def may_write(self, project: str) -> bool:
        """Whether the session may write ``project`` now (:func:`scope_session.may_write`)."""
        return scope_session.may_write(self.session, project)

    def write_names(self) -> str:
        if self.auto_locked and self.session.get("lock_state") in {None, "open"}:
            return "the first project it touches"
        return _names(self.session.get("write_projects")) or "no project"

    def names_of(self, ids: list[str]) -> str:
        """The projects' names: the session's own for those it reads, else asked for."""
        named = {
            str(p.get("id")): str(p.get("name") or p.get("id"))
            for p in self.session.get("read_projects") or []
        }
        return ", ".join(named.get(p) or _project_name(p) for p in dict.fromkeys(ids))


def operation_mode(entry: dict[str, Any]) -> str | None:
    """How a ``beakr api`` operation runs in a session; None when it writes no project content."""
    op = entry["op"]
    if op in _TOKEN_REFUSED_OPS:
        return CHECKED
    if op in _PERSON_OPS:
        return PERSON
    if entry.get("scope") in _CONTENT_SCOPES:
        return TOKEN
    if op in _TOKEN_OPS:
        return TOKEN
    return None


def run_gated(
    command: str,
    mode: str | None,
    params: dict[str, Any],
    invoke: Callable[[], T],
    *,
    targets: Callable[[], list[str] | None] | None = None,
) -> T | None:
    """Run ``invoke`` (the command ``command``) in a session when ``mode`` says it writes.

    ``params`` are the command's parsed parameters; ``targets`` names the projects
    it writes when they are known (default: from ``params``, :func:`_targets`).
    """
    if command == "compound" and params.get("hook"):
        from beakr_cli import agent_hooks

        if not agent_hooks.available():
            # A self-hosted build runs no hooks: no session is read or opened for one.
            return None
    if mode == PERSON:
        # A person's own approval: never from inside an agent conversation, where the
        # agent could approve what it uploaded itself.
        scope_session.refuse_inside_conversation(
            "beakr files approve" if command == "files approve" else f"beakr {command}",
            "Approve the file",
        )
    if mode is None or not ENABLED or _exempt_credential():
        return invoke()
    find_targets = targets or (lambda: _targets(command, params))
    if command == "compound" and params.get("hook"):
        return _run_hook(command, params, invoke, find_targets)
    held = _held(command, params, hook=False)
    if held is None:
        return invoke()
    projects = find_targets()
    if projects:
        _check_writes(held, projects)
    previous = os.environ.get(scope_session.SESSION_ENV)
    # Named in the environment so every client this command makes (and any
    # process it starts, such as a detached compound) uses the session.
    os.environ[scope_session.SESSION_ENV] = held.id
    try:
        if mode in {CHECKED, PERSON}:
            with scope_session.person_credential(unjoined=mode == PERSON):
                return invoke()
        return invoke()
    except BeakrError as exc:
        if exc.code == scope_session.WRITE_SCOPE and not isinstance(exc, SessionScopeError):
            raise _write_refusal(held, _refused_ids(held, exc)) from exc
        if exc.code in {scope_session.READ_ONLY, scope_session.LOCKED_TO_PROJECT} and not (
            isinstance(exc, SessionScopeError)
        ):
            # The API's words (it names the projects), with the fix that works here.
            raise scope_session.explain_lock_refusal(
                exc, held.session, shell=previous == held.id
            ) from exc
        if exc.status == 401:
            # Said while the session is still named here (restored below).
            explained = scope_session.explain_refusal(exc)
            if explained is not exc:
                raise explained from exc
        if isinstance(exc, PermissionDeniedError) and exc.code == "insufficient_scope":
            _say(
                f"This ran in session {held.id}, which may write: {held.write_names()}. "
                f"To write another project, add it with `beakr session edit {held.id}` "
                f"or at {held.picker_url}."
            )
        raise
    finally:
        if previous is None:
            os.environ.pop(scope_session.SESSION_ENV, None)
        else:
            os.environ[scope_session.SESSION_ENV] = previous


def install(app: Any, prefix: str = "") -> None:
    """Run each command of ``app`` named in :data:`WRITE_COMMANDS` through the gate.

    The gate runs once Click has parsed the command line (``--help`` and usage
    errors never ask for a session), with the parsed parameters.
    """
    import typer
    from typer.main import solve_typer_info_defaults

    from beakr_cli.command_surface import _command_name

    for info in app.registered_commands:
        path = f"{prefix}{_command_name(info)}"
        if path in WRITE_COMMANDS and not getattr(info.cls, "beakr_write_command", None):
            info.cls = _gated_class(path, info.cls or typer.core.TyperCommand)
    for group in app.registered_groups:
        name = solve_typer_info_defaults(group).name
        install(group.typer_instance, f"{prefix}{name} " if name else prefix)


def _gated_class(path: str, base: type) -> type:
    class GatedCommand(base):  # type: ignore[misc, valid-type]
        beakr_write_command = path

        def invoke(self, ctx: Any) -> Any:
            return run_gated(
                path,
                WRITE_COMMANDS[path],
                dict(ctx.params),
                lambda: super(GatedCommand, self).invoke(ctx),
            )

    return GatedCommand


def _run_hook(
    command: str,
    params: dict[str, Any],
    invoke: Callable[[], T],
    targets: Callable[[], list[str] | None],
) -> T | None:
    """A hook (``beakr compound --hook``): never asks and never waits.

    With a session open (named, or kept for this terminal or agent session) it
    compounds only into a project the session may write, and otherwise skips with
    one line in the hooks log. With none open it compounds into the folder's bound
    project as before: the person chose that project with ``beakr init``, so it is
    not the cross-project mistake sessions guard against. Either way it sends the
    person's credential (the compound routes take no session token).
    """
    try:
        held = _held(command, params, hook=True)
    except BeakrError as exc:
        _hook_log(f"{command} hook: could not read the session ({exc}); compounding without it.")
        held = None
    if held is None:
        with scope_session.person_credential():
            return invoke()
    if not held.active:
        _hook_log(
            f"{command} hook: skipped. Session {held.id} is open here but its projects are not "
            f"chosen yet; choose them at {held.picker_url}."
        )
        return None
    refused = [p for p in dict.fromkeys(targets() or []) if not held.may_write(p)]
    if refused:
        names = held.names_of(refused)
        _hook_log(
            f"{command} hook: skipped. Session {held.id} may not write {names} (it may write: "
            f"{held.write_names()}); let it with `beakr session edit {held.id}` or at "
            f"{held.picker_url}."
        )
        return None
    previous = os.environ.get(scope_session.SESSION_ENV)
    os.environ[scope_session.SESSION_ENV] = held.id
    try:
        with scope_session.person_credential():
            return invoke()
    finally:
        if previous is None:
            os.environ.pop(scope_session.SESSION_ENV, None)
        else:
            os.environ[scope_session.SESSION_ENV] = previous


# --- Which session ------------------------------------------------------------------------


def _exempt_credential() -> bool:
    """A credential that cannot hold a session, known without asking: a service account's
    file, or a session token itself."""
    from beakr_sdk.credentials import resolve_credential

    try:
        credential = resolve_credential()
    except BeakrError:
        return False  # no credential: the command fails as before, with the sign-in hint
    token = str(getattr(credential, "token", None) or "")
    return credential.source == "service_account" or token.startswith("bk_st_")


def _held(command: str, params: dict[str, Any], *, hook: bool) -> Held | None:
    """The session this write runs in; None where none can be held (run as before)."""
    from beakr_cli.sdk import get_sdk

    named = scope_session.shell_session_id()
    with get_sdk(scoped=False) as person:
        if named is not None:
            held = Held(_named(person, named))
        else:
            kept = _kept(person)
            if kept is not None:
                held = kept
            elif hook or command in NO_NEW_SESSION:
                return None
            else:
                started = _start(person, command)
                if started is None:
                    return None
                held = started
        if held.session.get("status") == "pending" and not hook:
            if _interactive():
                _choose_here(person, held, command, params)
            else:
                _wait_for_choice(person, held, command)
    scope_session.keep_remembered(held.session)
    if held.new and held.auto_locked:
        _say(
            f"Session {held.id}: Beakr locks it to the first project it touches, and a second "
            f"project read makes it read-only. {_reuse_line(held)}"
        )
    elif held.new:
        _say(
            f"Session {held.id}: writes {held.write_names()}. This terminal reuses it for "
            f"writes until `beakr session end {held.id}`. In another shell: "
            f"export {scope_session.SESSION_ENV}={held.id}"
        )
    return held


def _reuse_line(held: Held) -> str:
    if held.session.get("agent_session"):
        return "This agent conversation's commands and beakr mcp all run in it."
    return (
        f"This terminal reuses it until `beakr session end {held.id}`. In another shell: "
        f"export {scope_session.SESSION_ENV}={held.id}"
    )


def _named(person: Any, session_id: str) -> dict[str, Any]:
    """The session ``BEAKR_SCOPE_SESSION`` names; refused when it is not usable here."""
    if scope_session.load_state(session_id) is None:
        raise SessionScopeError(
            f"{scope_session.SESSION_ENV} names session {session_id}, but there is no token "
            "for it here.",
            code="session_not_found",
            fix=f"Run `beakr session start`, or `unset {scope_session.SESSION_ENV}`.",
        )
    try:
        session = person.request("GET", f"/v1/sessions/{session_id}")
    except NotFoundError as missing:
        if not scope_session.gone(missing):
            raise
        session = {"status": "ended"}
    if session.get("status") == "ended":
        # Refused, not replaced by another session: the shell chose this one, and
        # every read there would still be refused until it leaves it.
        raise scope_session.ended(session_id, nothing_written=True)
    return session


def _kept(person: Any) -> Held | None:
    """The session this terminal kept, while it is open."""
    session_id = scope_session.terminal_session_id()
    if session_id is None:
        return None
    try:
        session = person.request("GET", f"/v1/sessions/{session_id}")
    except NotFoundError as missing:
        if not scope_session.gone(missing):
            raise
        session = {"status": "ended"}
    if session.get("status") == "ended":
        scope_session.forget_terminal()
        scope_session.delete_state(session_id)
        return None
    return Held(session)


def _start(person: Any, command: str) -> Held | None:
    """Start a session for this terminal; None where the installation or credential has none."""
    folder = os.getcwd()
    remembered = scope_session.remembered_selection(folder)
    # At a terminal the picker shows the remembered choice; elsewhere it is used as is.
    lists = remembered if remembered and not _interactive() else None
    try:
        # In an agent conversation: its session (beakr mcp's), joined or started.
        session, state = scope_session.create_session(
            person,
            client_name="beakr-cli",
            label=f"beakr {command} in {Path(folder).name}",
            folder=folder,
            read=lists[0] if lists else None,
            write=lists[1] if lists else None,
            agent=scope_session.agent_session_id(),
        )
    except NotFoundError:
        return None  # an installation without sessions: as before
    except PermissionDeniedError as exc:
        # A service account, external user or end-user token cannot hold a session;
        # a session token cannot start one (it is already in one).
        if exc.code in {
            "insufficient_scope",
            "session_token_not_allowed",
            "session_scopes_disabled",
        }:
            return None
        raise
    scope_session.save_state(state)
    scope_session.remember_terminal(state.session_id, mode=session.get("mode"))
    return Held({k: v for k, v in session.items() if k != "token"}, new=True)


def _interactive() -> bool:
    from beakr_cli.output import is_interactive

    return is_interactive()


def _choose_here(person: Any, held: Held, command: str, params: dict[str, Any]) -> None:
    """The terminal picker, as ``beakr session start`` opens it; the choice is the person's."""
    from beakr_cli.commands.session import pick
    from beakr_cli.output import human_output_to_stderr
    from beakr_cli.project_binding import resolve_project

    remembered = scope_session.remembered_selection(os.getcwd())
    if remembered is None:
        # Nothing remembered here: the project the command names, ready to accept.
        explicit = [resolve_project(p) or p for p in _explicit_projects(params)]
        remembered = (explicit, explicit)
    _say(
        f"`beakr {command}` writes to a project, so it runs in a session: choose the projects "
        "this terminal may read and write (writing one also reads it)."
    )
    projects = list(person.paginate("/v1/projects", page_size=100))
    with human_output_to_stderr(True):
        picked = pick(projects, *remembered, picker_url=held.picker_url)
    if picked is None or not picked[0]:
        _end_quietly(person, held.id)
        raise SessionScopeError(
            "No projects chosen; nothing was written.",
            code="session_cancelled",
            fix="Run the command again and choose at least one project to write.",
        )
    read, write = picked
    held.session = person.request(
        "PATCH",
        f"/v1/sessions/{held.id}",
        json={"read_project_ids": read, "write_project_ids": write},
    )
    held.new = True


def _wait_for_choice(person: Any, held: Held, command: str) -> None:
    """Not at a terminal: print the picker link and wait, bounded, for the person to choose."""
    minutes = round(WAIT_SECONDS / 60)
    _say(
        f"`beakr {command}` writes to a Beakr project, so it runs in a session, and the person "
        "has not chosen its projects yet. Ask them to choose which projects it may read and "
        f"write at:\n  {held.picker_url}\n"
        f"Waiting up to {minutes} minute(s) for the choice (session {held.id})..."
    )
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        session = person.request("GET", f"/v1/sessions/{held.id}")
        status = session.get("status")
        if status == "active":
            held.session = session
            held.new = True
            return
        if status == "ended":
            scope_session.forget_terminal()
            raise SessionScopeError(
                f"Session {held.id} was ended before its projects were chosen; nothing was "
                "written.",
                code="session_ended",
                fix="Run the command again to start another.",
            )
        if time.monotonic() >= deadline:
            raise SessionScopeError(
                f"No projects were chosen for session {held.id} within {minutes} minute(s); "
                "nothing was written.",
                code=scope_session.NEEDS_SELECTION,
                fix=(
                    f"Ask the person to choose them at {held.picker_url} and run the command "
                    "again: this terminal reuses the session (or name it: "
                    f"{scope_session.SESSION_ENV}={held.id} beakr ...)."
                ),
            )
        _sleep(POLL_SECONDS)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _end_quietly(person: Any, session_id: str) -> None:
    try:
        person.request("POST", f"/v1/sessions/{session_id}/end")
    except BeakrError:
        pass
    scope_session.delete_state(session_id)
    scope_session.forget_terminal()


# --- Which projects a write reaches -------------------------------------------------------


def _explicit_projects(params: dict[str, Any]) -> list[str]:
    """The projects the command was given (``--project``/``--scope``), as ids."""
    found: list[str] = []
    for key in ("project", "scope"):
        value = params.get(key)
        values = value if isinstance(value, (list, tuple)) else [value]
        found.extend(str(v) for v in values if v)
    return found


def _targets(command: str, params: dict[str, Any]) -> list[str] | None:
    """The projects the command writes, where the CLI can tell before it runs; else None."""
    from beakr_cli.project_binding import resolve_project

    if command == "files approve":
        from beakr_cli.sdk import get_sdk

        with get_sdk(scoped=False) as person:
            project = (person.files.get(str(params.get("artifact_id"))) or {}).get("project_id")
        return [str(project)] if project else None
    if command == "compound":
        from beakr_cli.compounding import project_for_session

        project = project_for_session(params.get("project"), params.get("session_cwd"))
        return [project] if project else None
    if command in {"conversations import", "projects profile setup"}:
        project = resolve_project(params.get("project"))
        return [project] if project else None
    return [resolve_project(p) or p for p in _explicit_projects(params)] or None


def _check_writes(held: Held, projects: list[str]) -> None:
    refused = [p for p in dict.fromkeys(projects) if not held.may_write(p)]
    if refused:
        raise _write_refusal(held, refused)


def _lock_refusal(held: Held, refused: list[str]) -> SessionScopeError:
    """An auto_lock session's refusal, said before anything is sent, as the API words it."""
    return scope_session.lock_refusal(held.session, held.names_of(refused) or "that project")


def _write_refusal(held: Held, refused: list[str]) -> SessionScopeError:
    """The refusal of a write outside the session, naming the projects and how to add them."""
    if held.auto_locked:
        return _lock_refusal(held, refused)
    what = held.names_of(refused) or "that project"
    return SessionScopeError(
        f"Session {held.id} may not write to {what}; nothing was written. It may write: "
        f"{held.write_names()}.",
        code=scope_session.WRITE_SCOPE,
        fix=(
            f"Let the session write {what} with `beakr session edit {held.id}` (or at "
            f"{held.picker_url}), then retry. To work in another project instead, end it with "
            f"`beakr session end {held.id}` and run the command again."
        ),
    )


def _refused_ids(held: Held, exc: BeakrError) -> list[str]:
    """The projects a ``session_write_scope`` refusal names, less those the session may write.

    The API's message names, all by id, the refused projects, the session's write
    list, and the picker link (which holds the session's id).
    """
    named = dict.fromkeys(p.lower() for p in _UUID.findall(exc.message or ""))
    return [p for p in named if p not in held.writes() and p != held.id.lower()]


def explain_write_refusal(exc: BeakrError, session_id: str | None) -> BeakrError:
    """The API's ``session_write_scope`` refusal outside the gate, with names for its ids.

    The session is read with the person's own credential; when it cannot be, the
    API's refusal is returned as it is.
    """
    from beakr_cli.sdk import get_sdk

    chosen = str((exc.details or {}).get("session_id") or session_id or "")
    if not chosen:
        return exc
    try:
        with get_sdk(scoped=False) as person:
            held = Held(person.request("GET", f"/v1/sessions/{chosen}"))
    except BeakrError:
        return exc
    return _write_refusal(held, _refused_ids(held, exc))


def _project_name(project_id: str) -> str:
    from beakr_cli.sdk import get_sdk

    try:
        with get_sdk(scoped=False) as person:
            return str((person.projects.get(project_id) or {}).get("name") or project_id)
    except BeakrError:
        return project_id


def _names(projects: list[dict[str, Any]] | None) -> str:
    return ", ".join(str(p.get("name") or p.get("id")) for p in projects or [])


# --- Output -------------------------------------------------------------------------------


def _say(text: str) -> None:
    from beakr_cli.output import err_console

    err_console.print(text, markup=False, highlight=False, soft_wrap=True)


def _hook_log(message: str) -> None:
    from beakr_cli.hook_runtime import log_hook

    log_hook(message)
