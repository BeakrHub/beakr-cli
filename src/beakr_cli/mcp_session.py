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

Client support, checked 2026-11-08: Claude Code shows form elicitations as a
terminal dialog; Codex CLI shows them inline, its approval reviewer able to
decline. URL-mode elicitation is not dependable in either (Claude Code narrowed
its capability to form-only around 2.1.108), so the picker link is given as text
and the in-chat picker is a form: one Read and one Write checkbox per project,
plus "remember for this folder" when the server knows its folder. The form is
offered only for at most :data:`FORM_MAX_PROJECTS` projects; beyond that the link
is clearer than a long form.

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
#: Tools that run whatever the session's state: this module's own, and the
#: server's version and update tools, which reach no project.
UNGATED_TOOLS = frozenset({SESSION_SCOPE_TOOL, "beakr_version", "update_beakr", "discover_tools"})
#: Tools listed only while the server holds a session.
SESSION_TOOLS = frozenset({SESSION_SCOPE_TOOL})
FORM_MAX_PROJECTS = 25

SESSION_SCOPE_DESCRIPTION = (
    "Show which Beakr projects this session may read and write, or drop projects from it. "
    "The person chose the projects; this tool can only remove them (`drop` stops reading "
    "and writing a project, `drop_write` keeps reading it but stops writing), never add "
    "one. To add a project, give the person the session's picker link. Call it with no "
    "arguments to see the current scope."
)


def _error(code: str, message: str, fix: str | None, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error_code": code, "message": message, "fix": fix, **extra}


def needs_selection(session: scope_session.McpSession) -> dict[str, Any]:
    url = session.picker_url or ""
    return _error(
        scope_session.NEEDS_SELECTION,
        "This Beakr session has no projects yet: the person has not chosen which projects "
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
    """The form's schema (flat booleans) and each field's project id."""
    properties: dict[str, Any] = {}
    fields: dict[str, str] = {}
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


def selection_from(content: dict[str, Any], fields: dict[str, str]) -> tuple[list[str], list[str]]:
    """Read and write lists from the form; writing a project means reading it too."""
    read: list[str] = []
    write: list[str] = []
    for key, project_id in fields.items():
        if not content.get(key):
            continue
        if key.startswith("write_"):
            write.append(project_id)
        read.append(project_id)
    read = list(dict.fromkeys(read))
    return read, [p for p in dict.fromkeys(write)]


async def elicit_choice(server: Any, session: scope_session.McpSession) -> bool:
    """Ask the person in chat; True when they chose at least one project."""
    projects = await session.person_projects()
    if not projects or len(projects) > FORM_MAX_PROJECTS:
        return False
    schema, fields = picker_schema(projects, folder=session.folder)
    message = (
        "Choose the Beakr projects this agent session may read and write. Writing a project "
        f"also lets it read it. You can change this later at {session.picker_url}"
    )
    try:
        result = await server.get_context().session.elicit_form(message, schema)
    except Exception:  # noqa: BLE001 - a client that refuses the request gets the link
        return False
    if getattr(result, "action", None) != "accept" or not isinstance(result.content, dict):
        return False
    read, write = selection_from(result.content, fields)
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
    if not session.pending:
        return None
    if supports_form_elicitation(server) and await elicit_choice(server, session):
        return None
    return needs_selection(session)


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
            current = await session.narrow(drop=drop, drop_write=drop_write)
        else:
            current = await session.refresh()
    except BeakrError as exc:
        return _refused(exc)
    return scope_session.describe(current)


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
    """Register ``session_scope`` (listed only while a session is held)."""
    mcp.tool(
        name=SESSION_SCOPE_TOOL,
        description=SESSION_SCOPE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )(session_scope)
