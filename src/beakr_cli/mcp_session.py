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

``session_scope`` shows the session's projects and may only drop them: it never
adds one (checked here, and the API refuses a session token that tries with
``403 session_widen_refused``). It is listed only while the server holds a
session; the engine's shared tool surface does not describe it yet, so its
description is this module's.
"""

from __future__ import annotations

from typing import Any

from beakr_sdk.errors import BeakrError
from mcp.types import ToolAnnotations

from beakr_cli import scope_session

SESSION_SCOPE_TOOL = "session_scope"
SESSION_STATUS_TOOL = "session_status"
#: Tools that run whatever the session's state: this module's own, and the
#: server's version and update tools, which reach no project.
UNGATED_TOOLS = frozenset(
    {SESSION_SCOPE_TOOL, SESSION_STATUS_TOOL, "beakr_version", "update_beakr", "discover_tools"}
)
#: Tools listed only while the server holds a session.
SESSION_TOOLS = frozenset({SESSION_SCOPE_TOOL, SESSION_STATUS_TOOL})
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


SESSION_STATUS_DESCRIPTION = (
    "Show this Beakr session's state without changing it: its mode, and where the "
    "organization locks each session to the first project it touches (auto_lock), the "
    "project it is locked to, whether it is read-only (it read a second project, so writes "
    "are refused) and every project it has touched, in order. `trail=true` adds the "
    "session's read trail (newest first); `proposal_id` shows what that write's session had "
    "read before filing it, by project. Call it before writing when unsure "
    "which project this session may write."
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
        if (drop or drop_write) and scope_session.auto_locked(await session.refresh()):
            return _error(
                scope_session.MODE_AUTO_LOCK,
                "This session is auto_lock: Beakr locks it to the first project it touches, so "
                "it has no list of projects to drop from.",
                "Call session_status to see which project it is locked to.",
            )
        if drop or drop_write:
            current = await session.narrow(drop=drop, drop_write=drop_write)
        else:
            current = await session.refresh()
    except BeakrError as exc:
        return _refused(exc)
    return scope_session.describe(current)


async def session_status(trail: bool = False, proposal_id: str = "") -> dict[str, Any] | str:
    """The ``session_status`` tool (see :data:`SESSION_STATUS_DESCRIPTION`)."""
    from beakr_cli import session_trail
    from beakr_cli.mcp_errors import call_api

    session = scope_session.ACTIVE
    if session is None:
        return "This Beakr MCP server is not running in a session: it uses your own credential."
    try:
        await session.ensure_started()
        current = await session.refresh()
    except BeakrError as exc:
        return _refused(exc)
    lines = [scope_session.describe(current)]
    if trail:
        path = session_trail.trail_path(str(session.id))
        page = await call_api(
            lambda sdk: sdk.request("GET", path, params={"limit": session_trail.DEFAULT_LIMIT})
        )
        lines.append(session_trail.render_trail(page))
    if proposal_id.strip():
        path = session_trail.proposal_trail_path(proposal_id.strip())
        lines.append(
            session_trail.render_write_trail(await call_api(lambda sdk: sdk.request("GET", path)))
        )
    return "\n\n".join(lines)


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
    """Register ``session_scope`` and ``session_status`` (listed only while a session is held)."""
    mcp.tool(
        name=SESSION_SCOPE_TOOL,
        description=SESSION_SCOPE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )(session_scope)
    mcp.tool(
        name=SESSION_STATUS_TOOL,
        description=SESSION_STATUS_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )(session_status)
