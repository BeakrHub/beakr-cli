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

from dataclasses import dataclass, field
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
READ_EVERYTHING = "read_everything"

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


@dataclass
class Offer:
    """The choices the projects' boundaries allow (``POST /v1/sessions/options``).

    A form's fields are flat booleans, so a choice the boundaries forbid is left
    out rather than greyed out. An API without the options route allows them all,
    as before: the API still refuses a choice that breaks a boundary.
    """

    #: "Write all": reading and writing every project together is valid.
    write_all: bool = True
    #: "Read all" with per-project writes: some project can still be written.
    read_all: bool = True
    #: The projects that can be written with "Read all"; None for any.
    read_all_writes: set[str] | None = None
    #: The projects that can't be read, or written, even on their own.
    no_read: set[str] = field(default_factory=set)
    no_write: set[str] = field(default_factory=set)
    #: What "Read all, no writes" reads (the API's words), when it says.
    read_everything: str | None = None
    #: Why each left-out choice can't be used, for the person.
    why: list[str] = field(default_factory=list)


def _names(projects: list[dict[str, Any]], ids: set[str]) -> str:
    return ", ".join(str(p.get("name") or p.get("id")) for p in projects if str(p["id"]) in ids)


async def offer_for(session: scope_session.McpSession, projects: list[dict[str, Any]]) -> Offer:
    """What the in-chat form may offer for ``projects``, checked against their boundaries."""
    every = [str(project["id"]) for project in projects]
    alone = await session.options([], [])
    if alone is None:
        return Offer()
    whole = await session.options(every, every) or {}
    reading = await session.options(every, []) or {}
    offer = Offer(write_all=whole.get("valid") is not False)
    if not offer.write_all:
        offer.why.extend(str(r["message"]) for r in scope_session.selection_reasons(whole)[:1])
    rows = [r for r in reading.get("projects") or [] if isinstance(r, dict)]
    if reading.get("valid") is False:
        offer.read_all = False
        offer.why.extend(str(r["message"]) for r in scope_session.selection_reasons(reading)[:1])
    elif rows:
        # Reading every project, which can still be written? None: "Read all" is
        # then the same as "Read all, no writes", which is offered anyway.
        offer.read_all_writes = {str(r["id"]) for r in rows if r.get("can_write")}
        offer.read_all = bool(offer.read_all_writes)
    for row in alone.get("projects") or []:
        if not isinstance(row, dict):
            continue
        if row.get("can_read") is False:
            offer.no_read.add(str(row["id"]))
        if row.get("can_write") is False:
            offer.no_write.add(str(row["id"]))
    summary = (alone.get("read_everything") or {}).get("summary")
    offer.read_everything = str(summary) if summary else None
    return offer


def picker_schema(
    projects: list[dict[str, Any]], *, folder: str | None, offer: Offer | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """The form's schema (flat booleans) and each per-project field's project id.

    "Read all", "Write all" and "Read all, no writes" come first, each only where
    the boundaries allow it (``offer``); the per-project rows follow while there are
    at most :data:`FORM_MAX_PROJECTS` projects, without a Read or Write the
    boundaries forbid.
    """
    offer = offer or Offer()
    every = f"All {len(projects)} projects you can see now; projects created later are not added."
    properties: dict[str, Any] = {}
    if offer.read_all:
        writes = every
        if offer.read_all_writes is not None and len(offer.read_all_writes) < len(projects):
            writes = (
                f"{every} With it, only these can also be written: "
                f"{_names(projects, offer.read_all_writes)}."
            )
        properties[READ_ALL] = {
            "type": "boolean",
            "title": "Read all projects",
            "description": writes,
            "default": False,
        }
    if offer.write_all:
        properties[WRITE_ALL] = {
            "type": "boolean",
            "title": "Write all projects (also reads them)",
            "description": every,
            "default": False,
        }
    properties[READ_EVERYTHING] = {
        "type": "boolean",
        "title": "Read all projects, no writes",
        "description": offer.read_everything or "Reads every project you can read; writes nothing.",
        "default": False,
    }
    fields: dict[str, str] = {}
    if len(projects) <= FORM_MAX_PROJECTS:
        for index, project in enumerate(projects):
            name = str(project.get("name") or project.get("id"))
            for kind, label, left_out in (
                ("read", "Read", offer.no_read),
                ("write", "Write", offer.no_write),
            ):
                if str(project["id"]) in left_out:
                    continue
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


def _working_choices(offer: Offer, projects: list[dict[str, Any]]) -> str:
    """The choices that work, in words, for a person whose choice was refused."""
    choices = ["Read all projects, no writes"]
    if offer.write_all:
        choices.append("Write all projects")
    if offer.read_all:
        if offer.read_all_writes is not None and len(offer.read_all_writes) < len(projects):
            choices.append(
                f"Read all projects, writing only {_names(projects, offer.read_all_writes)}"
            )
        else:
            choices.append("Read all projects, with any writes")
    choices.append("projects chosen one by one, within their boundaries")
    return "; ".join(choices)


#: How many times the form is shown for one request: once, and again after a refusal.
FORM_ATTEMPTS = 2


async def elicit_choice(
    server: Any, session: scope_session.McpSession, *, replaced: bool = False
) -> bool | dict[str, Any]:
    """Ask the person in chat; True when they chose at least one project.

    Only choices the projects' boundaries allow are offered. A choice the API
    refuses anyway is asked again once, with why and the choices that work; refused
    again, the answer is the refusal with the picker link (a dict), never the API's
    bare 422.
    """
    projects = await session.person_projects()
    if not projects or len(projects) > scope_session.MAX_PROJECTS:
        return False
    try:
        offer = await offer_for(session, projects)
    except BeakrError:
        offer = Offer()
    schema, fields = picker_schema(projects, folder=session.folder, offer=offer)
    if fields:
        how = "Tick one of the choices for all projects, or choose per project below."
        later = f" You can change this later at {session.picker_url}"
    else:
        how = (
            f"You have {len(projects)} projects, too many to list here: tick one of the "
            f"choices for all projects, or choose some of them at {session.picker_url}"
        )
        later = ""
    message = (
        (f"{scope_session.REPLACED_MESSAGE} " if replaced else "")
        + f"Choose the Beakr projects this agent session may read and write. {how} "
        "All means the projects you have now; ones created later are not added. Writing a "
        f"project also lets it read it.{later}"
    )
    every = [str(project["id"]) for project in projects]
    refused: BeakrError | None = None
    for _ in range(FORM_ATTEMPTS):
        try:
            result = await server.get_context().session.elicit_form(message, schema)
        except Exception:  # noqa: BLE001 - a client that refuses the request gets the link
            break
        if getattr(result, "action", None) != "accept" or not isinstance(result.content, dict):
            break
        content = result.content
        read, write = selection_from(content, fields, every)
        if not (read or content.get(READ_EVERYTHING)):
            break
        try:
            await session.choose(
                read,
                write,
                remember=bool(content.get("remember")),
                read_everything=bool(content.get(READ_EVERYTHING)),
            )
        except BeakrError as exc:
            if scope_session.needs_person(exc):
                # Chosen with the key, the ticks are kept as a proposal: the person
                # confirms them in the browser, where they are preselected.
                url = scope_session.picker_url_of(exc) or session.picker_url or ""
                scope_session.open_in_browser(url)
                return _error(
                    scope_session.WIDEN_NEEDS_PERSON,
                    f"Confirm in your browser: {url}",
                    "The choice is proposed there, preselected; once the person confirms it, "
                    "retry.",
                    picker_url=url,
                )
            if exc.code != scope_session.SELECTION_INVALID:
                raise
            refused = exc
            reasons = [
                str(r["message"]) for r in scope_session.selection_reasons(exc) if r.get("message")
            ] or [exc.message]
            # Why a choice for all projects is left out is said only now, once the
            # person has picked something: before, it read as a refusal of nothing.
            left_out = [why for why in offer.why if why not in reasons]
            message = (
                "That choice can't be used: "
                + " ".join(reasons)
                + f" Choices that work: {_working_choices(offer, projects)}."
                + (" Not offered: " + " ".join(left_out) if left_out else "")
            )
            continue
        return not session.pending
    if refused is not None:
        return _error(
            scope_session.SELECTION_INVALID,
            scope_session.selection_invalid(refused).message,
            f"Show the user this link to choose the projects: {session.picker_url or ''}",
            picker_url=session.picker_url or "",
        )
    return False


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
    if supports_form_elicitation(server):
        chosen = await elicit_choice(server, session, replaced=replaced)
        if isinstance(chosen, dict):
            return chosen
        if chosen:
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


def _call_api() -> Any:
    """The API call the tools make (``mcp_tools_platform.call_api``, read when called)."""
    from beakr_cli import mcp_tools_platform

    return mcp_tools_platform.call_api


async def _status_text(current: dict[str, Any] | None) -> str:
    """``GET /v1/sessions/current/status`` in plain words. Not in a session (404): said
    so; an engine without the route (its bare 404) gets the session's own description."""
    from beakr_cli.capabilities import NOT_PROVIDED_CODE
    from beakr_cli.mcp_errors import ApiCallError

    try:
        status = await _call_api()(lambda sdk: sdk.request("GET", "/v1/sessions/current/status"))
    except ApiCallError as exc:
        if exc.fields.get("error_code") == NOT_PROVIDED_CODE and current is not None:
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
        # Without a session of its own (BEAKR_MCP_SESSIONS=off), the API answers for the
        # agent conversation this server's requests name, if any.
        return await _status_text(None)
    try:
        await session.ensure_started()
        current = await session.refresh()
    except BeakrError as exc:
        if exc.code != scope_session.SESSION_ENDED:
            return _refused(exc)
        # Ended (the person ended it on the web): the API's own answer for the
        # conversation, as the hosted server gives it (contract section 7).
        return await _ended_status(session, exc)
    try:
        text = await _status_text(current)
        # The session's id first: an agent asked which session it is in can say.
        lines = [text if text == NOT_IN_SESSION else f"Session: {session.id}\n{text}"]
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
    session: scope_session.McpSession, ended: BeakrError
) -> dict[str, Any] | str:
    """``session_status`` for a conversation whose session ended: the API's refusal
    (``409 session_ended``), asked as the person for the conversation's id, so the agent
    reads what the hosted server's ``session_status`` answers."""
    from beakr_cli.sdk import MCP_CLIENT_NAME, get_async_sdk

    agent = session.agent or session.ended_conversation
    if not agent:
        return _refused(ended)
    try:
        async with get_async_sdk(
            client_name=MCP_CLIENT_NAME, scoped=False, agent_session=agent
        ) as person:
            status = await person.request("GET", "/v1/sessions/current/status")
    except BeakrError as exc:
        return _refused(exc)
    return "\n".join(scope_session.status_lines(status))


async def start_session(
    read: list[str] | None = None,
    write: list[str] | None = None,
    read_everything: bool = False,
) -> dict[str, Any] | str:
    """The ``start_session`` tool (its description is the shared tool surface's)."""
    session = scope_session.ACTIVE
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
    if session is None:
        return await _declare_conversation(read, write, read_everything=read_everything)
    try:
        await session.ensure_started()
        current = await session.refresh()
    except BeakrError as exc:
        return _refused(exc)
    if session.pending and not session.agent:
        return await _the_persons_choice(session)
    started = _already_started(current)
    if started is not None:
        return started
    try:
        read_ids: list[str] | None = None
        write_ids: list[str] | None = None
        if read or write:
            read_ids, write_ids = await _resolve_lists(session, read, write)
        started = await session.declare(
            read=read_ids, write=write_ids, read_everything=read_everything
        )
    except BeakrError as exc:
        if scope_session.needs_person(exc):
            # The API keeps an agent's widening as a proposal: the person confirms it.
            return _proposed(
                scope_session.picker_url_of(exc) or session.picker_url or "",
                read,
                write,
                read_everything=read_everything,
                fix=exc.fix,
            )
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


def _already_started(session: dict[str, Any]) -> dict[str, Any] | None:
    """The refusal of ``start_session`` once the session has touched a project: its
    projects are fixed from then on, and only the person can end it."""
    touched = session.get("touched_projects") or []
    if not touched:
        return None
    names = ", ".join(str(p.get("name") or p.get("id")) for p in touched if isinstance(p, dict))
    url = session.get("picker_url") or ""
    return _error(
        scope_session.ALREADY_STARTED,
        f"This session has already worked in {names}, so its projects can't be chosen now.",
        # The API's own words for its 409.
        "End this session in Beakr (Settings, Sessions) and start a new agent conversation to "
        "choose its projects.",
        **({"picker_url": url} if url else {}),
    )


async def _the_persons_choice(session: scope_session.McpSession) -> dict[str, Any] | str:
    """``start_session`` on a pending session (an organization where every session must
    pick): the choice is the person's, never the agent's lists. They answer the in-chat
    form where the client has one, else at the picker link."""
    if _SERVER is not None and supports_form_elicitation(_SERVER):
        chosen = await elicit_choice(_SERVER, session)
        if isinstance(chosen, dict):
            return chosen
        if chosen:
            lines = [f"Session started. {session.session.get('summary') or ''}".strip()]
            lines.extend(scope_session.note_lines(session.session))
            return "\n".join(lines)
    return _persons_to_choose(session.picker_url or "")


def _proposed(
    url: str,
    read: list[str],
    write: list[str],
    *,
    read_everything: bool,
    fix: str | None = None,
) -> dict[str, Any]:
    """The answer to a declaration the API kept as a proposal: the person confirms it on
    the session's page, where it is preselected."""
    if read_everything:
        what = "read every project, write none"
    else:
        reads = ", ".join(dict.fromkeys([*read, *write]))
        what = f"read {reads}; write {', '.join(write) or 'nothing'}"
    scope_session.open_in_browser(url)
    return _error(
        scope_session.WIDEN_NEEDS_PERSON,
        f"Proposed for this session ({what}); only the person can confirm it. Confirm in "
        f"your browser: {url}",
        fix
        or "Show the person the link. The projects asked for are preselected there: they "
        "confirm with Save, then retry the call.",
        picker_url=url,
    )


def _persons_to_choose(url: str) -> dict[str, Any]:
    """The refusal of the agent's lists for a pending session: the person chooses."""
    return _error(
        scope_session.NEEDS_SELECTION,
        "In this organization the person chooses this session's projects, not the agent.",
        f"Show the user this link and ask them to choose the projects, then retry: {url}",
        picker_url=url,
    )


async def _declare_conversation(
    read: list[str], write: list[str], *, read_everything: bool
) -> dict[str, Any] | str:
    """``start_session`` without a session of this server's own (``BEAKR_MCP_SESSIONS=off``):
    the agent conversation's, by its id, as the remote server declares it."""
    from beakr_cli.mcp_errors import ApiCallError
    from beakr_cli.mcp_tools_platform import _project_by_name_or_id
    from beakr_cli.sdk import MCP_CLIENT_NAME

    agent = scope_session.agent_session_id()
    if not agent:
        return _error(
            "session_unavailable",
            "This connection has no agent session id (X-Beakr-Session), so it works as you, "
            "outside a session.",
            "Set BEAKR_SESSION for this MCP server (or let the client set its conversation id), "
            "or work without a session.",
        )
    try:
        # The person's key with X-Beakr-Session: the API answers for the conversation's
        # session once it has one.
        current = await _call_api()(lambda sdk: sdk.request("GET", "/v1/sessions/current"))
    except ApiCallError:
        current = None  # none yet: the API refuses a touched one (409) anyway
    if isinstance(current, dict) and current.get("agent_session") == agent:
        started = _already_started(current)
        if started is not None:
            return started
    body: dict[str, Any] = {"agent_session": agent, "client_name": MCP_CLIENT_NAME}
    if read_everything:
        body["read_everything"] = True
    else:
        write_ids = [str(await _project_by_name_or_id(ref)) for ref in write]
        read_ids = [str(await _project_by_name_or_id(ref)) for ref in read]
        body["read_project_ids"] = list(dict.fromkeys([*read_ids, *write_ids]))
        body["write_project_ids"] = write_ids
    try:
        started = await _call_api()(lambda sdk: sdk.request("POST", "/v1/sessions", json=body))
    except ApiCallError as exc:
        if exc.fields.get("error_code") == scope_session.WIDEN_NEEDS_PERSON:
            return _proposed(
                str((_details(exc.response) or {}).get("picker_url") or ""),
                read,
                write,
                read_everything=read_everything,
                fix=exc.fields.get("fix"),
            )
        reasons = [
            str(r["message"])
            for r in ((_details(exc.response) or {}).get("reasons") or [])
            if isinstance(r, dict) and r.get("message")
        ]
        if len(reasons) > 1:
            return _error(
                str(exc.fields.get("error_code")),
                "\n".join(f"- {r}" for r in reasons),
                exc.fields.get("fix"),
            )
        raise
    started = started if isinstance(started, dict) else {}
    lines = [f"Session started. {started.get('summary') or ''}".strip()]
    lines.extend(scope_session.note_lines(started))
    return "\n".join(lines)


def _details(response: Any) -> dict[str, Any] | None:
    """The error envelope's ``details`` (or FastAPI's ``detail``) of a refused call."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("details"), dict):
        return error["details"]
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else None


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


#: The server the tools run in, for the in-chat form ``start_session`` may open.
_SERVER: Any = None


def register(mcp: Any) -> None:
    """Register ``start_session`` and ``session_status`` (the shared surface's), and
    ``session_scope`` (listed only while a session is held)."""
    from beakr_cli import tool_surface

    global _SERVER
    _SERVER = mcp

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
