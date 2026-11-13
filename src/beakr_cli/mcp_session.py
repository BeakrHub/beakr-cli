"""The MCP side of session project scopes: the gate before each tool, the in-chat picker,
and the ``session_scope`` tool.

``beakr mcp`` holds one session (:mod:`beakr_cli.scope_session`). Before a tool
runs, :func:`gate`:

- starts the session if starting it failed earlier (a transient error never
  leaves the agent on the person's full credential);
- labels the session with the MCP client once it is known;
- while the person has not chosen any projects, offers the in-chat picker where
  the client supports MCP form elicitation (``clientCapabilities.elicitation``),
  and otherwise answers with the picker link and a short instruction to show it.

When the person ends the session (on its web page), the next request with its
token is refused (``401``). The server then starts a new pending session in its
place (``McpSession.recover``) and asks again the same way, saying the previous
session was ended; the new session reaches nothing until the person chooses. A
token that merely expired is renewed and the call made once more.

Client support, checked 2026-11-08: Claude Code shows form elicitations as a
terminal dialog; Codex CLI shows them inline, its approval reviewer able to
decline. URL-mode elicitation is not dependable in either (Claude Code narrowed
its capability to form-only around 2.1.108), so the picker link is given as text
and the in-chat picker is a form: "Read all projects" and "Write all projects"
first, then one Read and one Write checkbox per project, plus "remember for this
folder" when the server knows its folder. "All" is the projects the person can see
when they answer, sent as the lists; a project created later is not added. The
per-project rows are offered only for at most :data:`FORM_MAX_PROJECTS` projects
(beyond that the link is clearer than a long form), and the form not at all for
more than a session holds (:data:`scope_session.MAX_PROJECTS`).

``start_session`` declares the session up front, as the engine's remote MCP tool
does: picked (``read`` and ``write``) or ``read_everything``. The API applies it
only while the session has touched nothing (``409 session_already_started``) and
refuses lists that break a project boundary (``422 session_selection_invalid``,
each reason on its own line). ``session_status`` says, from
``GET /v1/sessions/current/status``, where the session can still write and why.

``start_session`` and ``session_status`` are the engine's shared tools (their
descriptions come from its tool surface) and are always listed; without a session
they say so. ``session_scope`` shows the session's projects and may only drop
them: it never adds one (checked here, and the API refuses a session token that
tries with ``403 session_widen_refused``). It is this server's own, listed only
while the server holds a session.
"""

from __future__ import annotations

from typing import Any

from beakr_sdk.errors import BeakrError
from mcp.types import ToolAnnotations

from beakr_cli import scope_session

SESSION_SCOPE_TOOL = "session_scope"
SESSION_STATUS_TOOL = "session_status"
START_SESSION_TOOL = "start_session"
#: Tools that run whatever the session's state: this module's own (start_session is
#: how a pending session gets its projects), and the server's version and update
#: tools, which reach no project.
UNGATED_TOOLS = frozenset(
    {
        SESSION_SCOPE_TOOL,
        SESSION_STATUS_TOOL,
        START_SESSION_TOOL,
        "beakr_version",
        "update_beakr",
        "discover_tools",
    }
)
#: Tools listed only while the server holds a session.
SESSION_TOOLS = frozenset({SESSION_SCOPE_TOOL})
FORM_MAX_PROJECTS = 25
READ_ALL = "read_all"
WRITE_ALL = "write_all"

SESSION_SCOPE_DESCRIPTION = (
    "Show which Beakr projects this session may read and write, or drop projects from it. "
    "The person chose the projects; this tool can only remove them (`drop` stops reading "
    "and writing a project, `drop_write` keeps reading it but stops writing), never add "
    "one. To add a project, give the person the session's picker link. Call it with no "
    "arguments to see the current scope."
)


def _error(code: str, message: str, fix: str | None, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error_code": code, "message": message, "fix": fix, **extra}


def needs_selection(session: scope_session.McpSession, *, replaced: bool = False) -> dict[str, Any]:
    url = session.picker_url or ""
    return _error(
        scope_session.NEEDS_SELECTION,
        f"{scope_session.REPLACED_MESSAGE} It has no projects until the person chooses them."
        if replaced
        else "This Beakr session has no projects yet: the person has not chosen which projects "
        "it may read and write.",
        f"Show the user this link and ask them to choose the projects, then retry: {url}",
        picker_url=url,
    )


def supports_form_elicitation(server: Any) -> bool:
    """Whether the client that sent ``initialize`` accepts form-mode elicitation."""
    try:
        params = server.get_context().session.client_params
    except (ValueError, AttributeError):
        return False
    capabilities = getattr(params, "capabilities", None)
    elicitation = getattr(capabilities, "elicitation", None)
    if elicitation is None:
        return False
    form = getattr(elicitation, "form", None)
    url = getattr(elicitation, "url", None)
    # An empty capability object means form mode (MCP 2025-11-25).
    return form is not None or url is None


def picker_schema(
    projects: list[dict[str, Any]], *, folder: str | None
) -> tuple[dict[str, Any], dict[str, str]]:
    """The form's schema (flat booleans) and each per-project field's project id.

    "Read all" and "Write all" come first; the per-project rows follow while there
    are at most :data:`FORM_MAX_PROJECTS` projects.
    """
    every = f"All {len(projects)} projects you can see now; projects created later are not added."
    properties: dict[str, Any] = {
        READ_ALL: {
            "type": "boolean",
            "title": "Read all projects",
            "description": every,
            "default": False,
        },
        WRITE_ALL: {
            "type": "boolean",
            "title": "Write all projects (also reads them)",
            "description": every,
            "default": False,
        },
    }
    fields: dict[str, str] = {}
    if len(projects) <= FORM_MAX_PROJECTS:
        for index, project in enumerate(projects):
            name = str(project.get("name") or project.get("id"))
            for kind, label in (("read", "Read"), ("write", "Write")):
                key = f"{kind}_{index}"
                properties[key] = {"type": "boolean", "title": f"{label}: {name}", "default": False}
                fields[key] = str(project["id"])
    if folder:
        properties["remember"] = {
            "type": "boolean",
            "title": f"Remember this choice for {folder}",
            "default": False,
        }
    return {"type": "object", "properties": properties}, fields


def selection_from(
    content: dict[str, Any], fields: dict[str, str], every: list[str]
) -> tuple[list[str], list[str]]:
    """Read and write lists from the form; writing a project means reading it too.

    "Write all" reads and writes ``every`` project; "Read all" reads them all and
    keeps the per-project Write ticks.
    """
    if content.get(WRITE_ALL):
        return list(every), list(every)
    read: list[str] = []
    write: list[str] = []
    for key, project_id in fields.items():
        if not content.get(key):
            continue
        if key.startswith("write_"):
            write.append(project_id)
        read.append(project_id)
    if content.get(READ_ALL):
        read = [*every, *read]
    return list(dict.fromkeys(read)), list(dict.fromkeys(write))


async def elicit_choice(
    server: Any, session: scope_session.McpSession, *, replaced: bool = False
) -> bool:
    """Ask the person in chat; True when they chose at least one project."""
    projects = await session.person_projects()
    if not projects or len(projects) > scope_session.MAX_PROJECTS:
        return False
    schema, fields = picker_schema(projects, folder=session.folder)
    if fields:
        how = "Tick Read all or Write all, or choose per project below."
        later = f" You can change this later at {session.picker_url}"
    else:
        how = (
            f"You have {len(projects)} projects, too many to list here: tick Read all or "
            f"Write all, or choose some of them at {session.picker_url}"
        )
        later = ""
    message = (
        (f"{scope_session.REPLACED_MESSAGE} " if replaced else "")
        + f"Choose the Beakr projects this agent session may read and write. {how} "
        "All means the projects you have now; ones created later are not added. Writing a "
        f"project also lets it read it.{later}"
    )
    try:
        result = await server.get_context().session.elicit_form(message, schema)
    except Exception:  # noqa: BLE001 - a client that refuses the request gets the link
        return False
    if getattr(result, "action", None) != "accept" or not isinstance(result.content, dict):
        return False
    every = [str(project["id"]) for project in projects]
    read, write = selection_from(result.content, fields, every)
    if not read:
        return False
    await session.choose(read, write, remember=bool(result.content.get("remember")))
    return not session.pending


async def gate(server: Any, tool: str, agent: str | None) -> dict[str, Any] | None:
    """An error result to answer instead of running ``tool``, or None to run it."""
    session = scope_session.ACTIVE
    if session is None or tool in UNGATED_TOOLS:
        return None
    try:
        await session.ensure_started()
        await session.name_client(agent)
        if not session.pending:
            return None
        await session.refresh()
    except BeakrError as exc:
        return _refused(exc)
    replaced = session.take_replaced()
    if not session.pending:
        return None
    if supports_form_elicitation(server) and await elicit_choice(
        server, session, replaced=replaced
    ):
        return None
    return needs_selection(session, replaced=replaced)


def was_replaced() -> bool:
    """Whether the server replaced its ended session and has not yet asked about it."""
    session = scope_session.ACTIVE
    return session is not None and session.replaced_from is not None


async def session_scope(
    drop: list[str] | None = None, drop_write: list[str] | None = None
) -> dict[str, Any] | str:
    """The ``session_scope`` tool (see :data:`SESSION_SCOPE_DESCRIPTION`)."""
    session = scope_session.ACTIVE
    if session is None:
        return "This Beakr MCP server is not running in a session: it uses your own credential."
    try:
        await session.ensure_started()
        if drop or drop_write:
            refreshed = await session.refresh()
            if scope_session.automatic(refreshed) or scope_session.reads_everything(refreshed):
                mode = scope_session.mode_of(refreshed)
                return _error(
                    NOT_PICKED,
                    f"This session is {str(mode).replace('_', ' ')}: it has no list of "
                    "projects to drop from.",
                    "Call session_status to see where it can still write.",
                )
        if drop or drop_write:
            current = await session.narrow(drop=drop, drop_write=drop_write)
        else:
            current = await session.refresh()
    except BeakrError as exc:
        return _refused(exc)
    return scope_session.describe(current)


#: ``session_scope`` asked to drop projects from an automatic or read_everything session.
NOT_PICKED = "session_not_picked"
#: Said when this server holds no session (``BEAKR_MCP_SESSIONS=off``, an installation
#: without sessions), as the remote server says it of a connection without one.
NOT_IN_SESSION = "This connection is not in a session: it works as you, and no session rules apply."


async def _status_text(current: dict[str, Any]) -> str:
    """``GET /v1/sessions/current/status`` in plain words; an engine without the route
    (its bare 404) gets the session's own description."""
    from beakr_cli.capabilities import NOT_PROVIDED_CODE
    from beakr_cli.mcp_errors import ApiCallError, call_api

    try:
        status = await call_api(lambda sdk: sdk.request("GET", "/v1/sessions/current/status"))
    except ApiCallError as exc:
        if exc.fields.get("error_code") == NOT_PROVIDED_CODE:
            return scope_session.describe(current)
        if exc.response.status_code == 404:
            return NOT_IN_SESSION
        raise
    return "\n".join(scope_session.status_lines(status))


async def session_status(trail: bool = False, proposal_id: str = "") -> dict[str, Any] | str:
    """The ``session_status`` tool (its description is the shared tool surface's)."""
    from beakr_cli import session_trail
    from beakr_cli.mcp_errors import ApiCallError, call_api

    session = scope_session.ACTIVE
    if session is None:
        return NOT_IN_SESSION
    try:
        await session.ensure_started()
        current = await session.refresh()
    except BeakrError as exc:
        if exc.code != scope_session.SESSION_ENDED:
            return _refused(exc)
        # Ended (the person ended it on the web): reads and writes are refused, but
        # the session is still described, with its trail, read as the person.
        return await _ended_status(session, exc, trail=trail, proposal_id=proposal_id)
    try:
        lines = [await _status_text(current)]
    except ApiCallError as exc:
        return _error(
            str(exc.fields.get("error_code") or "session_error"),
            str(exc.fields.get("message") or exc),
            exc.fields.get("fix"),
        )
    if trail:
        path = session_trail.trail_path(str(session.id))
        page = await call_api(
            lambda sdk: sdk.request("GET", path, params={"limit": session_trail.DEFAULT_LIMIT})
        )
        lines.append(session_trail.render_trail(page))
    if proposal_id.strip():
        # Never fetched here: the API refuses a session token, and the person's own
        # key would hand the agent other projects' reads.
        lines.append(session_trail.write_trail_as_person(proposal_id.strip()))
    return "\n\n".join(lines)


async def _ended_status(
    session: scope_session.McpSession, ended: BeakrError, *, trail: bool, proposal_id: str
) -> dict[str, Any] | str:
    """``session_status`` for a session that ended: what it was, its trail, how to go on."""
    from beakr_cli import session_trail

    try:
        record = await session.ended_record()
    except BeakrError:
        record = None
    if not record:
        return _refused(ended)
    lines = [scope_session.describe(record)]
    reads: list[tuple[str, dict[str, Any] | None, Any]] = []
    if trail:
        reads.append(
            (
                session_trail.trail_path(str(record.get("id"))),
                {"limit": session_trail.DEFAULT_LIMIT},
                session_trail.render_trail,
            )
        )
    for path, params, render in reads:
        try:
            lines.append(render(await session.ended_trail(path, params)))
        except BeakrError as exc:
            lines.append(f"Read trail not available: {exc.message}")
    if proposal_id.strip():
        # As for an open session: never with the person's key on the agent's behalf.
        lines.append(session_trail.write_trail_as_person(proposal_id.strip()))
    return "\n\n".join(lines)


async def start_session(
    read: list[str] | None = None,
    write: list[str] | None = None,
    read_everything: bool = False,
) -> dict[str, Any] | str:
    """The ``start_session`` tool (its description is the shared tool surface's)."""
    session = scope_session.ACTIVE
    if session is None:
        return _error(
            "session_unavailable",
            "This Beakr MCP server is not running in a session, so it works as you, outside a "
            "session.",
            "Restart the Beakr MCP server without BEAKR_MCP_SESSIONS=off to work in a session, "
            "or work without one.",
        )
    read = [r for r in read or [] if str(r).strip()]
    write = [w for w in write or [] if str(w).strip()]
    if read_everything and (read or write):
        return _error(
            "invalid_arguments",
            "read_everything takes no project lists: it reads every project and writes none.",
            "Pass read_everything alone, or read and write without it.",
        )
    if not (read_everything or read or write):
        return _error(
            "invalid_arguments",
            "Pass the projects to read and write, or read_everything=true.",
            "Call start_session with read (and write), or with read_everything=true. Without "
            "start_session the session is automatic.",
        )
    try:
        await session.ensure_started()
        read_ids: list[str] | None = None
        write_ids: list[str] | None = None
        if read or write:
            read_ids, write_ids = await _resolve_lists(session, read, write)
        started = await session.declare(
            read=read_ids, write=write_ids, read_everything=read_everything
        )
    except BeakrError as exc:
        reasons = [
            str(r["message"]) for r in scope_session.selection_reasons(exc) if r.get("message")
        ]
        if len(reasons) > 1:
            # Every reason, one per line, as the remote server says them.
            return _error(str(exc.code), "\n".join(f"- {r}" for r in reasons), exc.fix)
        return _refused(exc)
    lines = [f"Session started. {started.get('summary') or ''}".strip()]
    lines.extend(scope_session.note_lines(started))
    if started.get("status") == "pending":
        lines.append(
            f"Show the user this link to choose the projects: {started.get('picker_url') or ''}"
        )
    return "\n".join(lines)


async def _resolve_lists(
    session: scope_session.McpSession, read: list[str], write: list[str]
) -> tuple[list[str], list[str]]:
    """Project names or ids to ids, among the projects the person can read; writing a
    project reads it too."""
    projects = await session.person_projects(limit=10_000)

    def resolve(ref: str) -> str:
        text = str(ref).strip()
        for project in projects:
            if text == str(project.get("id")) or text == str(project.get("slug") or ""):
                return str(project["id"])
        named = [p for p in projects if str(p.get("name") or "").casefold() == text.casefold()]
        if len(named) == 1:
            return str(named[0]["id"])
        if named:
            raise scope_session.SessionScopeError(
                f"{len(named)} projects are named {text!r}.",
                code="project_ambiguous",
                fix="Pass the project's id (list_projects shows it).",
            )
        raise scope_session.SessionScopeError(
            f"No project named {text!r} among the projects you can read.",
            code="not_found",
            fix="Call list_projects for the names, and pass one of them (or its id).",
        )

    write_ids = list(dict.fromkeys(resolve(ref) for ref in write))
    read_ids = list(dict.fromkeys([*(resolve(ref) for ref in read), *write_ids]))
    return read_ids, write_ids


def _refused(exc: BeakrError) -> dict[str, Any]:
    """A local or API refusal as the tool's error, with the picker link when it has one."""
    url = scope_session.picker_url_of(exc)
    extra = {"picker_url": url} if url else {}
    return _error(exc.code or "session_error", exc.message, exc.fix, **extra)


def session_projects(session: scope_session.McpSession) -> list[dict[str, Any]]:
    """The session's readable projects, for ``list_projects`` under a session."""
    writable = set(scope_session.project_ids(session.session.get("write_projects")))
    return [
        {**project, "session_access": "read and write" if project.get("id") in writable else "read"}
        for project in session.session.get("read_projects") or []
    ]


def register(mcp: Any) -> None:
    """Register ``start_session`` and ``session_status`` (the shared surface's), and
    ``session_scope`` (listed only while a session is held)."""
    from beakr_cli import tool_surface

    mcp.tool(
        name=SESSION_SCOPE_TOOL,
        description=SESSION_SCOPE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )(session_scope)
    mcp.tool(
        name=SESSION_STATUS_TOOL,
        **tool_surface.tool_kwargs(SESSION_STATUS_TOOL),
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )(session_status)
    mcp.tool(
        name=START_SESSION_TOOL,
        **tool_surface.tool_kwargs(START_SESSION_TOOL),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
        ),
    )(start_session)
