"""``beakr session start|show|edit|end|ls|forget``: limit a shell to the projects you choose.

A session lets an agent (or any command) read only the projects you pick, and
write only the ones you allow; you can change both while it runs. ``beakr session
start`` opens one and prints the line that puts this shell in it::

    eval "$(beakr session start --env)"

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
from pathlib import Path
from typing import Any

import typer

from beakr_cli import scope_session
from beakr_cli.output import console, emit, err_console, is_interactive, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import prompt_text
from beakr_cli.sdk import get_sdk

app = typer.Typer(
    help=(
        "Limit a shell or agent session to the projects you choose. Commands that write "
        "into a project always run in one: outside a session, the first write in a "
        "terminal asks which projects it may write and the terminal keeps that session."
    )
)

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


def _person_project_refs(value: Any) -> Any:
    """Resolve --read/--write names with the person's own credential, never the shell's
    session token: the session may not read the project yet, or may have ended."""
    with scope_session.person_credential():
        return project_ref_callback(value)


_read_opt = typer.Option(
    [],
    "--read",
    "-r",
    help="A project the session may read (id, slug or name); repeatable.",
    callback=_person_project_refs,
)
_write_opt = typer.Option(
    [],
    "--write",
    "-w",
    help="A project the session may write (it may also read it); repeatable.",
    callback=_person_project_refs,
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
    with get_sdk(scoped=False) as sdk:
        for row in sdk.paginate("/v1/sessions"):
            if row.get("agent_session") == agent and row.get("status") != "ended":
                return str(row.get("id"))
    return None


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


def _render(data: dict[str, Any]) -> None:
    console.print(scope_session.describe(data), highlight=False, markup=False)


@app.command("start")
def start(
    read: list[str] = _read_opt,
    write: list[str] = _write_opt,
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
    """Start a session limited to the projects you choose, and put this shell in it.

    Without --read/--write (or --read-all/--write-all) it opens a checkbox picker
    (preselecting what you remembered for this folder); not at a terminal, the
    session starts pending and you choose at the link it prints.
    """
    folder = os.getcwd()
    lists: tuple[list[str], list[str]] | None = None
    with get_sdk(scoped=False) as sdk:
        if read_all or write_all:
            lists = _every(
                sdk, read, write, read_all=read_all, write_all=write_all, where=_PENDING_PAGE
            )
        elif read or write:
            lists = _lists(read, write)
        elif not pending:
            remembered = scope_session.remembered_selection(folder)
            if _interactive() and not env:
                start_read, start_write = remembered or ([], [])
                picked = pick(_projects(sdk), start_read, start_write)
                if picked is None:
                    raise typer.Exit(1)
                lists = picked
            elif remembered:
                lists = remembered
        session, state = scope_session.create_session(
            sdk,
            client_name="beakr-cli",
            label=label or f"beakr shell in {Path(folder).name}",
            folder=folder,
            read=lists[0] if lists else None,
            write=lists[1] if lists else None,
        )
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

    emit({k: v for k, v in session.items() if k != "token"}, json_out=json_out, render=render)


@app.command("show")
def show(
    session_id: str | None = typer.Argument(None, help="Defaults to this shell's session."),
    json_out: bool = _json_opt,
) -> None:
    """The session's projects: what it may read and write, and where to change them."""
    chosen = _session_id(session_id)
    with get_sdk(scoped=False) as sdk:
        session = sdk.request("GET", f"/v1/sessions/{chosen}")
    _maybe_remember(session)
    emit(session, json_out=json_out, render=_render, lines=_describe_lines)


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

    Where the organization locks sessions (auto_lock), it shows which read locked the
    session to its project and which made it read-only. Kept where the organization
    has the read trail turned on.
    """
    from beakr_cli.session_trail import render_trail, trail_path

    chosen = _session_id(session_id)
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    with get_sdk(scoped=False) as sdk:
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
    """Your open sessions (agents and shells), newest first."""
    with get_sdk(scoped=False) as sdk:
        rows = list(sdk.paginate("/v1/sessions", include_ended=all_sessions))

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
    remember: bool | None = typer.Option(
        None,
        "--remember/--no-remember",
        help="Reuse this choice for sessions started in this folder (.beakr/project.json).",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Change the session's projects with your own credential; it applies to its next request.

    --read/--write (or --read-all/--write-all) replace the lists; without them, at a
    terminal, the picker opens with the current choice.
    """
    chosen = _session_id(session_id)
    with get_sdk(scoped=False) as sdk:
        current = sdk.request("GET", f"/v1/sessions/{chosen}")
        where = f"on the session's page ({current.get('picker_url')})"
        lists: tuple[list[str], list[str]] | None
        if read_all or write_all:
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
        if lists is not None:
            body["read_project_ids"], body["write_project_ids"] = lists
        if remember is not None:
            body["remember_folder"] = remember
        if not body:
            raise typer.BadParameter(
                "give --read/--write, --read-all/--write-all, or --remember", param_hint="--read"
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
    """End the session: its token stops working at once."""
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
