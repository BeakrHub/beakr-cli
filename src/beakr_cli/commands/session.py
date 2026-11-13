"""``beakr session start|status|show|edit|end|ls|forget``: work in a session.

A session works one of three ways. Picked (``--read A,B --write A``): it reads only
the projects you pick and writes only the ones you allow; you can change both while
it runs. Read everything (``--read-everything``): it reads every project you can
read, locked ones included, and writes nothing. Automatic (``--automatic``, the
default where the organization allows it): it starts open and Beakr tracks what it
touches, so project boundaries decide where it can still write. Picked lists are
checked against the projects' boundaries before the session starts
(``POST /v1/sessions/options``); a choice that breaks one is refused with the reason.
``beakr session status`` says where a session can still write, and why.

``beakr session start`` opens one and prints the line that puts this shell in it::

    eval "$(beakr session start --env)"

Inside an agent conversation (Claude Code, Codex, Cursor, ``BEAKR_SESSION``) it
declares that conversation's own session instead, as the ``start_session`` MCP tool
does: only before the session has read or written anything.

From then on every ``beakr`` command in the shell sends the session's token
instead of your own credential, until ``beakr session end`` (or ``unset
BEAKR_SCOPE_SESSION``). The token stays in ``~/.beakr/sessions`` (0600); only the
session's id is in the environment. Without a session the CLI works as before.

These commands always act with your own credential, so ``edit`` can add projects
while the shell is in the session; the agent's token can only drop them.

Commands that write into a project always run in a session (see
:mod:`beakr_cli.write_session`): outside one, the first write in a terminal
opens the picker (or, not at a terminal, prints the picker link and waits) and
the terminal keeps the session for its next writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from beakr_sdk.errors import BeakrError, NotFoundError

from beakr_cli import scope_session
from beakr_cli.output import console, emit, err_console, is_interactive, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import prompt_text
from beakr_cli.scope_session import SessionScopeError
from beakr_cli.sdk import get_sdk

app = typer.Typer(
    help=(
        "Work in a session: picked (--read/--write), read everything, or automatic, where "
        "project boundaries decide where it can still write. Commands that write into a "
        "project always run in one: outside a session, the first write in a terminal asks "
        "which projects it may write and the terminal keeps that session."
    )
)

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


def _person_project_refs(value: Any) -> Any:
    """Resolve --read/--write names with the person's own credential, never the shell's
    session token: the session may not read the project yet, or may have ended.

    Each value may name several projects, comma-separated (``--read A,B``)."""
    if isinstance(value, (list, tuple)):
        value = [part.strip() for v in value for part in str(v).split(",") if part.strip()]
    with scope_session.person_credential():
        return project_ref_callback(value)


_read_opt = typer.Option(
    [],
    "--read",
    "-r",
    help="Projects the session may read (ids, slugs or names), comma-separated or repeated.",
    callback=_person_project_refs,
)
_write_opt = typer.Option(
    [],
    "--write",
    "-w",
    help="Projects the session may write (it also reads them), comma-separated or repeated.",
    callback=_person_project_refs,
)
_read_everything_opt = typer.Option(
    False,
    "--read-everything",
    help="Read every project you can read, locked ones included, and write nothing.",
)
_read_all_opt = typer.Option(
    False,
    "--read-all",
    help="Read every project you can see now (projects created later are not added).",
)
_write_all_opt = typer.Option(
    False,
    "--write-all",
    help="Read and write every project you can see now (projects created later are not added).",
)


def _session_id(session_id: str | None) -> str:
    """The session named, else this shell's, else the one this terminal kept, else this
    agent conversation's (``beakr mcp``'s, found among the person's open sessions)."""
    chosen = (
        session_id
        or scope_session.shell_session_id()
        or scope_session.terminal_session_id()
        or _conversation_session_id()
    )
    if not chosen:
        raise typer.BadParameter(
            f"name a session, or run this in a shell with {scope_session.SESSION_ENV} set "
            "(`beakr session ls` lists yours)",
            param_hint="SESSION_ID",
        )
    return chosen


def _conversation_session_id() -> str | None:
    """The open session of the agent conversation this command runs in, if it has one."""
    agent = scope_session.agent_session_id()
    if not agent:
        return None
    # Ended ones too: a conversation has one session, which never starts again.
    with get_sdk(scoped=False) as sdk:
        for row in sdk.paginate("/v1/sessions", include_ended=True):
            if row.get("agent_session") == agent:
                return str(row.get("id"))
    return None


# --- Inside an agent conversation --------------------------------------------------------
#
# The `beakr session` commands act with the person's own credential, which can read
# every session of theirs, unredacted. Inside an agent conversation (an agent
# conversation id is known: Claude Code, Codex, Cursor, BEAKR_SESSION) the agent runs
# them too, so reading another session (its read trail lists other projects' reads)
# would carry that content into this conversation. There they read through this
# conversation's own session token, which the API answers for that session only, and
# never name another session; ending another session is refused. From a plain
# terminal they act as the person, as before.


@dataclass
class _Conversation:
    """The agent conversation this command runs in, and its own session."""

    agent: str
    session_id: str | None
    #: Its token, when this terminal holds one; None once the session ended.
    token: str | None


def _conversation() -> _Conversation | None:
    """The agent conversation this command runs in; None in a plain terminal."""
    agent = scope_session.agent_session_id()
    if not agent:
        return None
    try:
        state = scope_session.conversation_state(agent)
    except SessionScopeError as exc:
        if exc.code != scope_session.SESSION_ENDED:
            raise
        # Ended, never started again: its own record is still the person's to read.
        return _Conversation(agent, _conversation_session_id(), None)
    return _Conversation(agent, state.session_id, state.token)


def _own(conversation: _Conversation) -> set[str]:
    """The sessions this conversation may read: its own, and this shell's (held here)."""
    return {s for s in (conversation.session_id, scope_session.shell_session_id()) if s}


def _confine(chosen: str | None, conversation: _Conversation) -> str:
    """The session to read inside an agent conversation: its own, never another."""
    chosen = chosen or conversation.session_id or scope_session.shell_session_id()
    if not chosen:
        raise typer.BadParameter(
            "this agent conversation has no session yet", param_hint="SESSION_ID"
        )
    if chosen not in _own(conversation):
        raise SessionScopeError(
            f"Inside an agent conversation, `beakr session` reads only this conversation's "
            f"session ({conversation.session_id}); {chosen} is another session.",
            code=OUTSIDE_CONVERSATION,
            fix=scope_session.OUTSIDE_CONVERSATION_FIX,
        )
    return chosen


def _reader(chosen: str, conversation: _Conversation | None) -> Any:
    """The client that reads ``chosen``: the conversation's (or this shell's) own token
    inside an agent conversation, else the person's own credential."""
    if conversation is None:
        return get_sdk(scoped=False)
    if chosen == scope_session.shell_session_id():
        return get_sdk()  # this shell's session token
    if conversation.token:
        return get_sdk(token=conversation.token)
    return get_sdk(scoped=False)  # the conversation's own ended session, as the person


#: The refusal of another session from inside an agent conversation.
OUTSIDE_CONVERSATION = scope_session.OUTSIDE_CONVERSATION


def _lists(read: list[str], write: list[str]) -> tuple[list[str], list[str]]:
    """De-duplicated lists; writing a project means reading it too."""
    write = list(dict.fromkeys(write))
    return list(dict.fromkeys([*read, *write])), write


def _projects(sdk: Any) -> list[dict[str, Any]]:
    return list(sdk.paginate("/v1/projects", page_size=100))


#: Where to choose when "all" is more than a session holds, before the session exists.
_PENDING_PAGE = "on the web page (`beakr session start --pending` prints its link)"


def _too_many(count: int, where: str) -> str:
    return (
        f"You can see {count} projects and a session may name at most "
        f"{scope_session.MAX_PROJECTS}: tick them by number, or choose {where}."
    )


def pick(
    projects: list[dict[str, Any]],
    read: list[str],
    write: list[str],
    *,
    prompt: Any = None,
    picker_url: str | None = None,
) -> tuple[list[str], list[str]] | None:
    """A checkbox picker in the terminal: toggle Read and Write by number.

    ``r 1,3`` toggles reading projects 1 and 3, ``w 2`` toggles writing project 2
    (which also reads it), ``r all`` / ``w all`` tick every project listed (the
    projects there are now, not ones created later), ``r none`` / ``w none`` clear
    the column (no reads means no writes), Enter accepts and ``q`` cancels (None).
    """
    ask = prompt or (
        lambda text: prompt_text(text, flag_hint="--read/--write or --read-all/--write-all")
    )
    where = f"on the session's page ({picker_url})" if picker_url else _PENDING_PAGE
    chosen_read = set(read)
    chosen_write = set(write)
    ids = [str(p.get("id")) for p in projects]
    while True:
        print_table(
            ["#", "Read", "Write", "Project"],
            [
                [
                    str(index),
                    "[x]" if pid in chosen_read else "[ ]",
                    "[x]" if pid in chosen_write else "[ ]",
                    short(str(project.get("name") or pid), 60),
                ]
                for index, (pid, project) in enumerate(zip(ids, projects, strict=True), start=1)
            ],
            title="Projects this session may use",
        )
        answer = str(
            ask(
                "Toggle with `r 1,3` or `w 2`; `r all` / `w all` tick every current project "
                "(`r none` / `w none` clear); Enter to accept, q to cancel"
            )
        ).strip()
        if not answer:
            picked_read = [p for p in ids if p in chosen_read]
            if len(picked_read) > scope_session.MAX_PROJECTS:
                err_console.print(
                    f"{len(picked_read)} projects are ticked and a session may name at most "
                    f"{scope_session.MAX_PROJECTS}: untick some, or choose {where}.",
                    markup=False,
                )
                continue
            return picked_read, [p for p in ids if p in chosen_write]
        if answer.lower() in {"q", "quit", "cancel"}:
            return None
        kind, _, numbers = answer.lower().partition(" ")
        numbers = numbers.strip()
        if kind not in {"r", "w"} or not numbers:
            err_console.print(
                "Type `r` or `w` and project numbers, `all` or `none`, e.g. `r 1,3` or `w all`."
            )
            continue
        if numbers == "all":
            if len(ids) > scope_session.MAX_PROJECTS:
                err_console.print(_too_many(len(ids), where), markup=False)
                continue
            chosen_read.update(ids)
            if kind == "w":
                chosen_write.update(ids)
            continue
        if numbers == "none":
            chosen_write.clear()
            if kind == "r":
                chosen_read.clear()
            continue
        for part in numbers.replace(",", " ").split():
            if not part.isdigit() or not 1 <= int(part) <= len(ids):
                err_console.print(f"No project number {part}.", markup=False)
                continue
            pid = ids[int(part) - 1]
            if kind == "r":
                if pid in chosen_read:
                    chosen_read.discard(pid)
                    chosen_write.discard(pid)
                else:
                    chosen_read.add(pid)
            elif pid in chosen_write:
                chosen_write.discard(pid)
            else:
                chosen_write.add(pid)
                chosen_read.add(pid)


def _every(
    sdk: Any,
    read: list[str],
    write: list[str],
    *,
    read_all: bool,
    write_all: bool,
    where: str,
) -> tuple[list[str], list[str]]:
    """The lists with --read-all/--write-all applied: every project the person can see
    now, refused when that is more than a session may name."""
    every = [str(p.get("id")) for p in _projects(sdk)]
    if len(every) > scope_session.MAX_PROJECTS:
        raise typer.BadParameter(
            f"you can see {len(every)} projects and a session may name at most "
            f"{scope_session.MAX_PROJECTS}: name them with --read/--write, or choose {where}",
            param_hint="--read-all/--write-all",
        )
    if write_all:
        write = [*every, *write]
    return _lists([*every, *read], write)


def _interactive() -> bool:
    return is_interactive()


def _pick_valid(sdk: Any, read: list[str], write: list[str]) -> tuple[list[str], list[str]]:
    """The terminal picker, again until the choice keeps every project's boundary."""
    projects = _projects(sdk)
    while True:
        picked = pick(projects, read, write)
        if picked is None:
            raise typer.Exit(1)
        try:
            check_selection(sdk, *picked)
        except SessionScopeError as refused:
            err_console.print(f"{refused.message}\n{refused.fix}", markup=False, highlight=False)
            read, write = picked
            continue
        return picked


def _render(data: dict[str, Any]) -> None:
    console.print(scope_session.describe(data), highlight=False, markup=False)


#: Where the picker's lists are checked against the projects' boundaries.
OPTIONS_PATH = "/v1/sessions/options"


def check_selection(sdk: Any, read: list[str], write: list[str]) -> dict[str, Any] | None:
    """Check picked lists against the projects' boundaries before starting
    (``POST /v1/sessions/options``); raise the API's reasons when they break one.

    None from an engine without the route (its bare 404): the session start checks
    them anyway.
    """
    from beakr_cli.capabilities import is_bare_not_found

    try:
        options = sdk.request(
            "POST", OPTIONS_PATH, json={"read_project_ids": read, "write_project_ids": write}
        )
    except NotFoundError as exc:
        if is_bare_not_found(exc.response):
            return None
        raise
    if isinstance(options, dict) and options.get("valid") is False:
        raise scope_session.selection_invalid(
            options,
            fix=(
                "Change --read or --write: a project you read that locks its content limits "
                "writes to it, and a project that takes no content from others can be written "
                "only by a session that reads it alone."
            ),
        )
    return options if isinstance(options, dict) else None


def _refuse_together(**given: bool) -> None:
    """Refuse modes given together (``--read-everything`` with lists, ``--automatic`` with
    either)."""
    named = [flag for flag, on in given.items() if on]
    if len(named) > 1:
        raise typer.BadParameter(
            f"{' and '.join(named)} can't be combined: a session is picked (--read/--write), "
            "read everything (--read-everything) or automatic (--automatic).",
            param_hint=named[0],
        )


def _org_session_mode(sdk: Any) -> str | None:
    """The organization's ``session_mode`` (``automatic`` or ``must_pick``), when it says."""
    from beakr_cli.project_binding import credential_org_id

    org_id = credential_org_id()
    if not org_id:
        return None
    try:
        settings = sdk.request("GET", scope_session.isolation_path(org_id))
    except BeakrError:
        return None
    mode = settings.get("session_mode") if isinstance(settings, dict) else None
    return str(mode) if mode else None


def _refuse_automatic_where_must_pick(sdk: Any) -> None:
    if _org_session_mode(sdk) == scope_session.MUST_PICK:
        raise SessionScopeError(
            "Your organization makes every session choose its projects (session mode "
            "must_pick), so an automatic session can't be started.",
            code="session_mode_must_pick",
            fix="Start it with --read/--write, or with --read-everything.",
        )


def _start_line(session: dict[str, Any]) -> str:
    """The one line an agent gets back: the API's summary, else the lists."""
    if session.get("summary"):
        return str(session["summary"])

    def names(key: str) -> str:
        return ", ".join(str(p.get("name") or p.get("id")) for p in session.get(key) or [])

    return (
        f"Reads: {names('read_projects') or 'none'}. Writes: {names('write_projects') or 'none'}."
    )


def _declare_in_conversation(
    agent: str,
    lists: tuple[list[str], list[str]] | None,
    *,
    read_everything: bool,
    json_out: bool,
) -> None:
    """``beakr session start`` inside an agent conversation: declare its own session."""
    session = scope_session.declare_conversation(
        agent,
        read=lists[0] if lists else None,
        write=lists[1] if lists else None,
        read_everything=read_everything,
    )

    def render(data: dict[str, Any]) -> None:
        console.print(f"Session started. {_start_line(data)}", markup=False, highlight=False)
        for line in scope_session.note_lines(data):
            console.print(line, markup=False, highlight=False)
        if data.get("status") == "pending":
            console.print(
                f"Show the user this link to choose the projects: {data.get('picker_url')}",
                markup=False,
                highlight=False,
            )

    def lines(data: dict[str, Any]) -> list[str]:
        return [f"Session started. {_start_line(data)}", *scope_session.note_lines(data)]

    emit(
        {k: v for k, v in session.items() if k != "token"},
        json_out=json_out,
        render=render,
        lines=lines,
    )


@app.command("start")
def start(
    read: list[str] = _read_opt,
    write: list[str] = _write_opt,
    read_everything: bool = _read_everything_opt,
    automatic: bool = typer.Option(
        False,
        "--automatic",
        help="Start open: Beakr tracks what the session touches, and project boundaries "
        "decide where it can still write.",
    ),
    read_all: bool = _read_all_opt,
    write_all: bool = _write_all_opt,
    label: str | None = typer.Option(None, "--label", help="How the session is shown to you."),
    pending: bool = typer.Option(
        False, "--pending", help="Start without projects; choose them at the picker link."
    ),
    env: bool = typer.Option(
        False,
        "--env",
        help='Print only the export line, for `eval "$(beakr session start --env)"`.',
    ),
    json_out: bool = _json_opt,
) -> None:
    """Start a session and put this shell in it: picked, read everything, or automatic.

    --read A,B --write A picks its projects, checked first against their boundaries
    (a choice that breaks one is refused with the reason); --read-everything reads
    every project and writes none; --automatic starts it open. With none of them it
    opens a checkbox picker (preselecting what you remembered for this folder); not
    at a terminal, the session starts pending and you choose at the link it prints.

    Inside an agent conversation it declares that conversation's own session, before
    it has read or written anything, as the start_session MCP tool does.
    """
    picked_lists = bool(read or write or read_all or write_all)
    _refuse_together(
        **{
            "--read/--write": picked_lists,
            "--read-everything": read_everything,
            "--automatic": automatic,
            "--pending": pending,
        }
    )
    agent = scope_session.agent_session_id()
    folder = os.getcwd()
    lists: tuple[list[str], list[str]] | None = None
    with get_sdk(scoped=False) as sdk:
        if read_all or write_all:
            lists = _every(
                sdk, read, write, read_all=read_all, write_all=write_all, where=_PENDING_PAGE
            )
        elif read or write:
            lists = _lists(read, write)
        if agent:
            if pending or env or label:
                raise SessionScopeError(
                    "Inside an agent conversation `beakr session start` declares that "
                    "conversation's own session: --pending, --env and --label are for a shell "
                    "session.",
                    code=scope_session.OUTSIDE_CONVERSATION,
                    fix="Give --read/--write, --read-everything or --automatic alone.",
                )
            if lists is not None:
                check_selection(sdk, *lists)
            elif automatic:
                _refuse_automatic_where_must_pick(sdk)
        elif lists is not None:
            check_selection(sdk, *lists)
        elif automatic:
            _refuse_automatic_where_must_pick(sdk)
        elif not (pending or read_everything):
            remembered = scope_session.remembered_selection(folder)
            if _interactive() and not env:
                lists = _pick_valid(sdk, *(remembered or ([], [])))
            elif remembered:
                lists = remembered
        if agent:
            session = None
        else:
            session, state = scope_session.create_session(
                sdk,
                client_name="beakr-cli",
                label=label or f"beakr shell in {Path(folder).name}",
                folder=folder,
                read=lists[0] if lists else None,
                write=lists[1] if lists else None,
                read_everything=read_everything,
            )
    if agent:
        _declare_in_conversation(agent, lists, read_everything=read_everything, json_out=json_out)
        return
    assert session is not None
    scope_session.save_state(state)
    export = f"export {scope_session.SESSION_ENV}={state.session_id}"
    if env:
        print(export)
        if session.get("status") == "pending":
            err_console.print(
                f"Choose the session's projects at {session.get('picker_url')}",
                markup=False,
                highlight=False,
            )
        else:
            err_console.print(_start_line(session), markup=False, highlight=False)
        return

    def render(data: dict[str, Any]) -> None:
        _render(data)
        err_console.print(
            f"\nPut this shell in the session (its commands then use only these projects):\n"
            f"  {export}\n"
            f'or start it with: eval "$(beakr session start --env)"\n'
            "End it with `beakr session end`.",
            markup=False,
            highlight=False,
        )

    emit(
        {k: v for k, v in session.items() if k != "token"},
        json_out=json_out,
        render=render,
        lines=_describe_lines,
    )


@app.command("show")
def show(
    session_id: str | None = typer.Argument(None, help="Defaults to this shell's session."),
    json_out: bool = _json_opt,
) -> None:
    """The session's projects: what it may read and write, and where to change them.

    Inside an agent conversation it shows only that conversation's own session.
    """
    conversation = _conversation()
    if conversation is not None:
        chosen = _confine(session_id, conversation)
        with _reader(chosen, conversation) as sdk:
            session = sdk.request("GET", f"/v1/sessions/{chosen}")
        emit(session, json_out=json_out, render=_render, lines=_describe_lines)
        return
    ended_here = None
    if session_id is None and not _current_session_id():
        # This terminal's session was ended (`beakr session end`): say so, not usage.
        ended_here = scope_session.terminal_recorded_id()
    chosen = ended_here or _session_id(session_id)
    with get_sdk(scoped=False) as sdk:
        session = sdk.request("GET", f"/v1/sessions/{chosen}")
    if ended_here and session.get("status") == "ended":
        at = str(session.get("ended_at") or "").replace("T", " ")[:16]
        line = (
            f"This terminal's session {chosen} ended" + (f" at {at} UTC" if at else "") + ". "
            "Start a new one with `beakr session start`."
        )
        emit(
            session,
            json_out=json_out,
            render=lambda _data: console.print(line, markup=False, highlight=False),
            lines=lambda _data: [line],
        )
        return
    _maybe_remember(session)
    emit(session, json_out=json_out, render=_render, lines=_describe_lines)


@app.command("status")
def status(
    session_id: str | None = typer.Argument(
        None, help="Defaults to this shell's, this terminal's or this agent conversation's session."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Where the session can still write, and why: its mode, what it has read, the
    projects it can't write with the reason, and its last boundary events.

    Inside an agent conversation it shows only that conversation's own session.
    """
    conversation = _conversation()
    if conversation is not None:
        chosen: str | None = (
            _confine(session_id, conversation)
            if (session_id or conversation.session_id or scope_session.shell_session_id())
            else None
        )
    else:
        chosen = (
            session_id or scope_session.shell_session_id() or scope_session.terminal_session_id()
        )
    if not chosen:
        line = scope_session.NOT_IN_SESSION
        emit(
            {"in_session": False, "message": line},
            json_out=json_out,
            render=lambda _data: console.print(line, markup=False, highlight=False),
            lines=lambda _data: [line],
        )
        return
    with _reader(chosen, conversation) as sdk:
        status = _status_of(sdk, chosen, conversation)

    def text(data: dict[str, Any]) -> list[str]:
        if "lock_state" in data and "write" not in data:
            return scope_session.describe(data).splitlines()  # an engine without the route
        return scope_session.status_lines(data, events=10)

    emit(
        status,
        json_out=json_out,
        render=lambda data: console.print("\n".join(text(data)), markup=False, highlight=False),
        lines=text,
    )


def _status_of(sdk: Any, chosen: str, conversation: _Conversation | None) -> dict[str, Any]:
    """``GET /v1/sessions/{id}/status`` (``current`` with a session token); the session
    itself from an engine without the route."""
    from beakr_cli.capabilities import is_bare_not_found

    own_token = conversation is not None and (
        chosen == scope_session.shell_session_id() or conversation.token
    )
    path = "/v1/sessions/current/status" if own_token else f"/v1/sessions/{chosen}/status"
    try:
        return sdk.request("GET", path)  # type: ignore[no-any-return]
    except NotFoundError as exc:
        if not is_bare_not_found(exc.response):
            raise
    return sdk.request("GET", f"/v1/sessions/{chosen}")  # type: ignore[no-any-return]


def _current_session_id() -> str | None:
    """A session in use here without asking the API: this shell's, or this terminal's."""
    return scope_session.shell_session_id() or scope_session.terminal_session_id()


def _describe_lines(session: dict[str, Any]) -> list[str]:
    """Piped (an agent reading it): the same lines a person sees, mode and lock included."""
    return scope_session.describe(session).splitlines()


@app.command("trail")
def trail(
    session_id: str | None = typer.Argument(
        None, help="Defaults to this shell's, this terminal's or this agent conversation's session."
    ),
    limit: int = typer.Option(
        50, "--limit", "-n", min=1, max=200, help="Most reads to show (newest first)."
    ),
    cursor: str | None = typer.Option(None, "--cursor", help="Continue from an earlier page."),
    json_out: bool = _json_opt,
) -> None:
    """The session's read trail, newest first: what each read was, which projects it
    touched and what it returned.

    With project boundaries it shows the session's declaration, which read locked it
    to a project, which made it read-only, and each refusal (``boundary:*`` rows).
    Kept where the organization has the read trail turned on.
    """
    from beakr_cli.session_trail import render_trail, trail_path

    conversation = _conversation()
    chosen = _confine(session_id, conversation) if conversation else _session_id(session_id)
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    with _reader(chosen, conversation) as sdk:
        page = sdk.request("GET", trail_path(chosen), params=params)

    def text(data: Any) -> str:
        return render_trail(data, session_id=chosen)

    emit(
        page,
        json_out=json_out,
        render=lambda data: console.print(text(data), highlight=False, markup=False),
        lines=lambda data: text(data).splitlines(),
    )


@app.command("ls")
def list_sessions(
    all_sessions: bool = typer.Option(False, "--all", help="Include ended sessions."),
    json_out: bool = _json_opt,
) -> None:
    """Your open sessions (agents and shells), newest first.

    Inside an agent conversation only that conversation's own session is listed.
    """
    conversation = _conversation()
    if conversation is not None and conversation.token:
        # The API lists a session token only its own session.
        with get_sdk(token=conversation.token) as sdk:
            rows = list(sdk.paginate("/v1/sessions", include_ended=all_sessions))
    else:
        with get_sdk(scoped=False) as sdk:
            rows = list(sdk.paginate("/v1/sessions", include_ended=all_sessions))
        if conversation is not None:
            # Its session ended (no token): only its own row, read as the person.
            rows = [r for r in rows if r.get("agent_session") == conversation.agent]

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            err_console.print("No open sessions.")
            return

        def names(row: dict[str, Any], key: str) -> str:
            return ", ".join(str(p.get("name") or p.get("id")) for p in row.get(key) or [])

        print_table(
            ["ID", "Client", "Status", "Reads", "Writes", "Started"],
            [
                [
                    str(r.get("id")),
                    short(r.get("label") or r.get("client_name"), 30),
                    str(r.get("status")),
                    short(names(r, "read_projects"), 40),
                    short(names(r, "write_projects"), 40),
                    str(r.get("created_at") or "")[:16],
                ]
                for r in data
            ],
            title=f"Sessions ({len(data)})",
            whole=("ID",),
        )

    emit(rows, json_out=json_out, render=render)


@app.command("edit")
def edit(
    session_id: str | None = typer.Argument(None, help="Defaults to this shell's session."),
    read: list[str] = _read_opt,
    write: list[str] = _write_opt,
    read_all: bool = _read_all_opt,
    write_all: bool = _write_all_opt,
    read_everything: bool = _read_everything_opt,
    remember: bool | None = typer.Option(
        None,
        "--remember/--no-remember",
        help="Reuse this choice for sessions started in this folder (.beakr/project.json).",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Change the session's projects with your own credential; it applies to its next request.

    --read/--write (or --read-all/--write-all) replace the lists, checked first against
    the projects' boundaries; --read-everything makes it read every project and write
    none. Without them, at a terminal, the picker opens with the current choice. An
    automatic or read-everything session that has touched a project can't be changed
    (409 session_already_started).
    """
    scope_session.refuse_inside_conversation("beakr session edit", "Change a session's projects")
    _refuse_together(
        **{
            "--read/--write": bool(read or write or read_all or write_all),
            "--read-everything": read_everything,
        }
    )
    chosen = _session_id(session_id)
    with get_sdk(scoped=False) as sdk:
        current = sdk.request("GET", f"/v1/sessions/{chosen}")
        where = f"on the session's page ({current.get('picker_url')})"
        lists: tuple[list[str], list[str]] | None
        if read_everything:
            lists = None
        elif read_all or write_all:
            lists = _every(sdk, read, write, read_all=read_all, write_all=write_all, where=where)
        elif read or write:
            lists = _lists(read, write)
        elif _interactive():
            lists = pick(
                _projects(sdk),
                scope_session.project_ids(current.get("read_projects")),
                scope_session.project_ids(current.get("write_projects")),
                picker_url=current.get("picker_url"),
            )
            if lists is None:
                raise typer.Exit(1)
        else:
            lists = None
        body: dict[str, Any] = {}
        if read_everything:
            body["read_everything"] = True
        elif lists is not None:
            check_selection(sdk, *lists)
            body["read_project_ids"], body["write_project_ids"] = lists
        if remember is not None:
            body["remember_folder"] = remember
        if not body:
            raise typer.BadParameter(
                "give --read/--write, --read-all/--write-all, --read-everything or --remember",
                param_hint="--read",
            )
        session = sdk.request("PATCH", f"/v1/sessions/{chosen}", json=body)
    if remember is False:
        scope_session.forget_selection(Path(session.get("folder") or os.getcwd()))
    _maybe_remember(session)
    emit(session, json_out=json_out, render=_render)


@app.command("end")
def end(
    session_id: str | None = typer.Argument(None, help="Defaults to this shell's session."),
    json_out: bool = _json_opt,
) -> None:
    """End the session: its token stops working at once.

    Inside an agent conversation only that conversation's own session (or this
    shell's) may be ended here.
    """
    conversation = _conversation()
    if conversation is not None:
        chosen = session_id or conversation.session_id or scope_session.shell_session_id()
        if not chosen or chosen not in _own(conversation):
            raise SessionScopeError(
                f"Inside an agent conversation, `beakr session end` ends only this "
                f"conversation's session ({conversation.session_id}).",
                code=OUTSIDE_CONVERSATION,
                fix=(
                    "End other sessions from the web app or a terminal outside an agent "
                    "conversation. Commands typed with `!` in Claude Code share the agent's "
                    "environment, so they are confined the same way."
                ),
            )
    else:
        chosen = _session_id(session_id)
    with get_sdk(scoped=False) as sdk:
        session = sdk.request("POST", f"/v1/sessions/{chosen}/end")
    scope_session.delete_state(chosen)

    def render(data: dict[str, Any]) -> None:
        err_console.print(f"Ended session {data.get('id')}.", markup=False)
        if data.get("agent_session"):
            err_console.print(
                "It was an agent conversation's session, which is never started again: "
                "Beakr refuses that conversation's requests from now on. "
                + scope_session.NEW_CONVERSATION_FIX,
                markup=False,
            )
        if scope_session.shell_session_id() == chosen:
            err_console.print(
                f"Leave it in this shell with: unset {scope_session.SESSION_ENV}", markup=False
            )

    emit(session, json_out=json_out, render=render, json_when_piped=False)


@app.command("forget")
def forget(json_out: bool = _json_opt) -> None:
    """Stop reusing a remembered session choice in this folder."""
    path = scope_session.forget_selection(Path.cwd())

    def render(data: dict[str, Any]) -> None:
        if data["binding"] is None:
            err_console.print("No session choice is remembered here.")
        else:
            err_console.print(f"Forgot the session choice in {data['binding']}.", markup=False)

    emit(
        {"forgotten": path is not None, "binding": str(path) if path else None},
        json_out=json_out,
        render=render,
        json_when_piped=False,
    )


def _maybe_remember(session: dict[str, Any]) -> None:
    scope_session.keep_remembered(session)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="session")
