"""MCP tools for the developer platform: session context, skills, conversations.

None of these is a wiki operation -- those stay behind ``knowledge_base`` and
``knowledge_base_write`` -- so they are this surface's own tools.
:mod:`beakr_cli.mcp_server` imports them and calls :func:`register_platform_tools`
on its server. This module never imports the server, so either can be imported
first without a cycle.

Every result is bounded: an MCP client pays for each character in context, and
an unbounded skill body or conversation list would crowd out the user's work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from beakr_cli import mcp_integration_results as integration_results
from beakr_cli import mcp_result, tool_surface
from beakr_cli.capabilities import NOT_PROVIDED_CODE
from beakr_cli.mcp_errors import ApiCallError, call_api
from beakr_cli.scope import scope_params

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

#: Upper bound on any text a tool returns.
MAX_TEXT_CHARS = 20_000
#: Upper bound on list lengths a tool renders.
MAX_LIST_ITEMS = 50
#: Largest context budget a caller may request.
MAX_CONTEXT_BUDGET = 20_000
#: Messages accepted by one import call.
MAX_IMPORT_MESSAGES = 2_000

_CONTEXT_FORMATS = ("markdown", "claude_md", "agents_md", "json")
_CONVERSATION_CLIENTS = ("claude-code", "codex", "chatgpt", "generic")


def _page_of(result: Any, *keys: str) -> tuple[list[dict[str, Any]], bool, str | None]:
    """A REST page's rows, whether more follow, and the cursor that continues it."""
    rows = _as_list(result, *keys)
    if not isinstance(result, dict):
        return rows, False, None
    cursor = result.get("next_cursor")
    has_more = bool(result.get("has_more")) and bool(cursor)
    return rows, has_more, (str(cursor) if has_more else None)


# One result shape on both MCP servers (SPEC-488): the engine's
# beakr/api/mcp_remote/formatting.py helpers, copied into beakr_cli.mcp_result.
_page_result = mcp_result.page_result
_capped_result = mcp_result.capped_result


def _cursor(cursor: str) -> str | None:
    return (cursor or "").strip() or None


def _page_size(limit: int | None, default: int = MAX_LIST_ITEMS) -> int:
    return max(1, min(int(limit or default), MAX_LIST_ITEMS))


def _expected_base_index(exc: ApiCallError) -> int | None:
    """The stored count from a 409 refusing an import's ``base_index``, if that is the 409."""
    response = exc.response
    if response.status_code != 409:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    for container in (body.get("error"), body.get("detail")) if isinstance(body, dict) else ():
        if isinstance(container, dict) and isinstance(container.get("expected_base_index"), int):
            return int(container["expected_base_index"])
    return None


def _as_list(result: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in keys:
            if isinstance(result.get(key), list):
                return result[key]
    return []


async def context(
    project: str = "",
    question: str = "",
    format: str = "markdown",
    budget_chars: int = 8_000,
) -> str:
    """Load onboarding context for this session: who the user is, the org and project
    profiles, their memories, what changed recently, relevant skills and entities.

    Use this ONCE at the start of a task (or when switching projects) before you
    search, so you start from what the team already knows. Pass ``question`` to
    focus the context on the task at hand. Do not use it to answer a specific
    factual question -- use research or knowledge_base for that.

    Args:
        project: Project name or ID to focus on. Omit for org-wide context.
        question: The task or question, to rank what is included.
        format: markdown (default), claude_md, agents_md or json.
        budget_chars: Approximate size of the result (max 20000).
    """
    if format not in _CONTEXT_FORMATS:
        return f"Invalid format '{format}'. Use one of: {', '.join(_CONTEXT_FORMATS)}."
    # The route takes a project id; agents pass the name list_projects printed.
    # Omitted, the context stays org-wide, as the tool says.
    project_id = await _project_by_name_or_id(project) if project.strip() else None
    data = await call_api(
        lambda sdk: sdk.engine.context(
            project_id=project_id,
            question=question or None,
            format=format,
            budget_chars=max(500, min(budget_chars, MAX_CONTEXT_BUDGET)),
        )
    )
    content = str((data or {}).get("content") or "")
    if not content:
        return "No context available yet for this scope."
    return content


def _match_skill(skills: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    lowered = ref.strip().lower()
    for skill in skills:
        if str(skill.get("id", "")) == ref or str(skill.get("slug", "")).lower() == lowered:
            return skill
    for skill in skills:
        if str(skill.get("name", "")).lower() == lowered:
            return skill
    return None


#: Library pages read while looking a skill up by slug or name.
_SKILL_LOOKUP_PAGES = 20


def _skill_list(skills_: list[dict[str, Any]]) -> str:
    if not skills_:
        return "No skills in your library."
    lines = [
        f"- {s.get('slug', '')}: {s.get('name', '')}"
        + (f" -- {s['description']}" if s.get("description") else "")
        for s in skills_[:MAX_LIST_ITEMS]
    ]
    lines.append("\nCall skills with a slug to read its SKILL.md.")
    return "\n".join(lines)


async def skills(skill: str = "", cursor: str = "", limit: int = MAX_LIST_ITEMS) -> dict[str, Any]:
    """List the user's Beakr skills, or read one skill's SKILL.md.

    Use without arguments to see which team procedures exist (name, slug, one-line
    description) when a task might follow an established workflow. Use with a
    slug or id to read that skill's full instructions before you follow it. Do
    not use it to search knowledge-base pages -- use knowledge_base for that.

    Args:
        skill: A skill slug, name or id. Omit to list skills.
        cursor: The next_cursor from the previous page's coverage.
        limit: Skills per page (1-50).
    """
    if not skill:
        page = await call_api(
            lambda sdk: sdk.api.skills.list_skills(
                installed=True, cursor=_cursor(cursor), limit=_page_size(limit)
            )
        )
        rows, has_more, next_cursor = _page_of(page, "data", "skills")
        return _page_result(
            _skill_list(rows),
            tool="skills",
            returned=min(len(rows), MAX_LIST_ITEMS),
            has_more=has_more,
            next_cursor=next_cursor,
        )

    match = None
    page_cursor: str | None = None
    for _ in range(_SKILL_LOOKUP_PAGES):
        page = await call_api(
            lambda sdk, c=page_cursor: sdk.api.skills.list_skills(
                installed=True, cursor=c, limit=MAX_LIST_ITEMS
            )
        )
        rows, has_more, page_cursor = _page_of(page, "data", "skills")
        match = _match_skill(rows, skill)
        if match is not None or not has_more:
            break
    if match is None:
        return {
            "content": f"No skill '{skill}' in your library. Call skills with no argument to "
            "list them."
        }
    bundle = await call_api(lambda sdk: sdk.skills.bundle(match["id"]))
    files = (bundle or {}).get("files") or {}
    skill_md = files.get("SKILL.md")
    if not skill_md:
        return {"content": f"Skill '{match.get('slug')}' has no SKILL.md."}
    others = sorted(path for path in files if path != "SKILL.md")
    footer = ""
    if others:
        footer = "\n\nOther files in this skill: " + ", ".join(others[:MAX_LIST_ITEMS])
    return {"content": str(skill_md) + footer}


def _conversation_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No matching conversations."
    lines = []
    for item in items:
        title = item.get("title") or "(untitled)"
        when = str(item.get("updated_at") or item.get("created_at") or "")[:10]
        lines.append(f"- {title} ({item.get('client', '')}, {when}, id: {item.get('id')})")
        lines.extend(_compound_note(item.get("compound_status")))
        if item.get("summary"):
            lines.append(f"  {' '.join(str(item['summary']).split())[:500]}")
    return "\n".join(lines)


async def conversations_search(
    query: str = "",
    client: str = "",
    project: str = "",
    limit: int = 10,
    cursor: str = "",
) -> dict[str, Any]:
    """Search your past conversations: sessions imported from Claude Code, Codex or
    ChatGPT, and your Beakr chats.

    Use when the user refers to earlier work ("what did we do last week on the
    importer?") or before redoing a task that may already have been worked
    through in another session. With a query it searches each conversation's
    index (title, summary, the entities and pages it touched) and ranks by how
    much of the query matches; returns titles, summaries, what each session
    found, and ids. Without a query it lists your most recent imported sessions.
    Do not use it for org knowledge -- research and knowledge_base cover that.

    Args:
        query: What the conversation was about (a question works).
        client: Only imported sessions from claude-code, codex, chatgpt or generic.
        project: Project name or ID to restrict to.
        limit: Conversations per page (1-25 with a query, 1-50 without).
        cursor: The next_cursor from the previous page's coverage (same query and filters).
    """
    # The routes take a project id; agents pass the name list_projects printed.
    project_id = await _project_by_name_or_id(project) if project.strip() else None
    if query and not client:
        return await _search_index(query, project_id=project_id, limit=limit, cursor=cursor)
    page_size = _page_size(limit, default=10)
    page = await call_api(
        lambda sdk: sdk.api.conversations.list_conversations(
            client=client or None,
            project_id=project_id,
            q=query or None,
            limit=page_size,
            cursor=_cursor(cursor),
        )
    )
    rows, has_more, next_cursor = _page_of(page, "data")
    rows = rows[:page_size]
    return _page_result(
        _conversation_lines(rows),
        tool="conversations_search",
        returned=len(rows),
        has_more=has_more,
        next_cursor=next_cursor,
        same_args="and the same filters",
    )


_SEARCH_LIMIT = 25


def _compound_note(status: Any) -> list[str]:
    if status and status not in ("done", "pending", "none"):
        # partial/skipped/failed keep their unprocessed messages for a retry.
        return [f"  compound: {status}"]
    return []


async def _search_index(
    query: str, *, project_id: str | None, limit: int, cursor: str
) -> dict[str, Any]:
    page = await call_api(
        lambda sdk: sdk.api.conversations.search(
            q=query,
            project_id=project_id,
            match="any",
            limit=max(1, min(limit, _SEARCH_LIMIT)),
            cursor=_cursor(cursor),
        )
    )
    hits, has_more, next_cursor = _page_of(page, "data")
    if not hits:
        text = "No matching conversations."
    else:
        lines = []
        for hit in hits:
            title = hit.get("title") or "(untitled)"
            when = str(hit.get("updated_at") or "")[:10]
            kind = "imported session" if hit.get("kind") == "imported" else "Beakr chat"
            lines.append(f"- {title} ({kind}, {when}, id: {hit.get('id')})")
            lines.extend(_compound_note(hit.get("compound_status")))
            if hit.get("summary"):
                lines.append(f"  {' '.join(str(hit['summary']).split())[:500]}")
            findings = hit.get("findings") or []
            if findings:
                titles = [f.get("page_title") for f in findings if f.get("page_title")]
                lines.append(
                    f"  findings: {len(findings)}"
                    + (f" ({', '.join(titles[:5])})" if titles else "")
                )
            outputs = hit.get("outputs") or []
            if outputs:
                lines.append("  outputs: " + ", ".join(str(o.get("title")) for o in outputs[:5]))
        text = "\n".join(lines)
    return _page_result(
        text,
        tool="conversations_search",
        returned=len(hits),
        has_more=has_more,
        next_cursor=next_cursor,
        same_args="and the same query",
    )


#: The summary_required refusal in this tool's terms, as the hosted tool words it:
#: the route's own fix names ``store_full_text``, ``compound`` and ``hold_text``,
#: which this tool does not take, so an agent following it could not comply.
_IMPORT_SUMMARY_REQUIRED = (
    "These messages came with no summary, and this tool keeps a session's summary, never "
    "its full text, so nothing from them would be kept."
)
_IMPORT_SUMMARY_FIX = (
    "Call import_conversation again with `summary`: what was done, decided and learned in "
    "the session, in a few sentences."
)


def _summary_required(exc: ApiCallError) -> ApiCallError:
    """``exc`` with its message and fix in import_conversation's own parameters."""
    reworded = ApiCallError(
        f"{_IMPORT_SUMMARY_REQUIRED} (summary_required) To fix: {_IMPORT_SUMMARY_FIX}",
        request=exc.request,
        response=exc.response,
    )
    reworded.fields = {
        **exc.fields,
        "message": _IMPORT_SUMMARY_REQUIRED,
        "fix": _IMPORT_SUMMARY_FIX,
    }
    return reworded


async def import_conversation(
    client: str,
    session_id: str,
    messages: list[dict] | None = None,
    title: str = "",
    summary: str = "",
    project: str = "",
    files_touched: list[str] | None = None,
    base_index: int | None = None,
) -> str:
    """Save this (or another) agent session into the user's private Beakr conversations.

    Use when the user asks to save the session, or at the end of a substantial
    session when the user has agreed to session capture. Prefer a concise
    ``summary`` of what was decided and learned over pasting every message.
    To add to a session imported before, send only the new messages and set
    ``base_index`` to how many messages were imported before; without
    ``base_index`` the messages are read as the WHOLE session and replace what
    was stored after the first difference. Do not use it to write
    knowledge-base pages -- use knowledge_base_write for that.

    Args:
        client: claude-code, codex, chatgpt or generic.
        session_id: Stable id of the session.
        messages: [{role: user|assistant, content}] in order.
        title: Short title.
        summary: What was done, decided and learned.
        project: Project name or ID it belongs to.
        files_touched: Paths edited in the session.
        base_index: Messages of this session already imported; the position the
            sent messages start at. Omit when sending the whole session.
    """
    if client not in _CONVERSATION_CLIENTS:
        return f"Invalid client '{client}'. Use one of: {', '.join(_CONVERSATION_CLIENTS)}."
    if not (messages or summary):
        return "Provide messages or a summary to import."
    body: dict[str, Any] = {
        "client": client,
        "session_id": session_id,
        # An agent-composed import carries no host marker that a person typed
        # the session, so personal learning never reads it.
        "interactive": False,
        # No store_full_text: the person's full-text setting for this client
        # decides (PROSE-392), as it does for the hosted MCP tool.
    }
    if messages:
        body["messages"] = [
            {"role": m.get("role"), "content": m.get("content")}
            | ({"at": m["at"]} if m.get("at") else {})
            for m in messages[:MAX_IMPORT_MESSAGES]
            if isinstance(m, dict)
        ]
    if title:
        body["title"] = title
    if summary:
        body["summary"] = summary
    if project.strip():
        # The route takes a project id; agents pass the name list_projects printed.
        body["project_id"] = await _project_by_name_or_id(project)
    if files_touched:
        body["files_touched"] = files_touched[:500]
    if base_index is not None:
        if base_index < 0:
            return "base_index must be zero or more."
        body["base_index"] = base_index
    try:
        # The body's keys are ``import_``'s parameters; absent ones are omitted.
        result = await call_api(lambda sdk: sdk.conversations.import_(**body))
    except ApiCallError as exc:
        if exc.fields.get("error_code") == "summary_required":
            raise _summary_required(exc) from None
        expected = _expected_base_index(exc)
        if expected is None:
            raise
        return (
            f"Beakr holds {expected} message(s) of this session, fewer than base_index "
            f"{base_index}. Send the messages from position {expected} on with "
            f"base_index={expected}."
        )
    verb = "Imported" if (result or {}).get("created") else "Updated"
    return (
        f"{verb} conversation {(result or {}).get('conversation_id')} "
        f"(stored: {(result or {}).get('stored', 'summary')})."
    )


async def org_audit_log(limit: int = 25, before: str = "", cursor: str = "") -> dict[str, Any]:
    """Read the organization's audit trail (sign-ins, key and policy changes), newest first.

    Use when the user asks who changed access, created or revoked a key or
    changed the auth policy. Requires an organization admin and the audit:read
    scope. Read-only: credentials and policy are changed in the web app or with
    the `beakr org` commands, never from here.

    Args:
        limit: Events per page, newest first (1-50).
        before: Only events older than this timestamp (an ISO time).
        cursor: The next_cursor from the previous page's coverage.
    """
    # The SDK resolves the org the way requests run: the override, the CLI
    # sign-in's org, else the server's active org.
    page = await call_api(
        lambda sdk: sdk.org.audit.list(
            limit=_page_size(limit, default=25),
            before=before or None,
            cursor=_cursor(cursor),
        )
    )
    events, has_more, next_cursor = _page_of(page, "data")
    text = (
        "\n".join(
            f"- {str(e.get('created_at') or '')[:19]} {e.get('action')} "
            f"by {e.get('actor_user_id') or 'system'}"
            + (f" on {e['target_resource_id']}" if e.get("target_resource_id") else "")
            for e in events
        )
        if events
        else "No audit events."
    )
    return _page_result(
        text,
        tool="org_audit_log",
        returned=len(events),
        has_more=has_more,
        next_cursor=next_cursor,
    )


#: Characters of a file returned per read_file call.
READ_FILE_CHARS = 20_000
#: Candidate files an ambiguous path's error lists.
_AMBIGUOUS_LISTED = 10


async def read_file(
    artifact_id: str = "",
    path: str = "",
    offset: int = 0,
    limit: int = READ_FILE_CHARS,
    project: str = "",
) -> str | dict[str, Any]:
    """A window of a document's extracted text, as the hosted read_file reads it.

    ``artifact_id`` (or ``path``) may be a file id, a version id, a citation's
    readable reference or ``source_ref``, a file's path as a read prints it, or
    a citation token; with `` › <unit>`` (or ``#<unit>``) after one (``memo.pdf › p. 7``)
    the window is the text around that place. One route resolves and reads all
    of them (``POST /v1/artifacts/read``), so a reference that reaches no
    readable file is one answer whatever the reason. ``project`` (or ``scope``)
    keeps the read to that project's files; one that names no readable project
    is refused with the closest names (:class:`ScopeNotFound`).
    """
    reference = str(path or artifact_id or "").strip()
    if not reference:
        return _read_error(
            "invalid_arguments",
            "Pass `artifact_id`: a file id, a citation's reference from a Knowledge read "
            "(memo.pdf › p. 7), or a file's path from its Sources (optionally with "
            "' › <unit>').",
            "Copy the `read_file <id>`, a citation's reference or the path from a Knowledge "
            "result's Sources.",
        )
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or READ_FILE_CHARS), READ_FILE_CHARS))
    # Sent only when named, so an unscoped read is the same request as before.
    scoped = {"project": await _project_id(project)} if project.strip() else {}
    try:
        data = await call_api(
            lambda sdk: sdk.files.read(reference, offset=offset, max_chars=limit, **scoped)
        )
    except ApiCallError as exc:
        status = exc.response.status_code
        if status == 404:
            # The hosted read_file's error exactly (beakr/api/mcp_remote/tools.py).
            return _read_error(
                None,
                "No readable file matches this reference. Pass `artifact_id`: the file id, "
                "reference, source_ref or path from a Knowledge result's Sources "
                "(location.file_id, or `read_file <id>`).",
            )
        if status == 409:
            return _read_conflict(exc)
        raise
    return mcp_result.file_read(dict(data or {}))


def _read_conflict(exc: ApiCallError) -> dict[str, Any]:
    """A 409 from the read route: an ambiguous path (with its candidates) or no text."""
    fields = exc.fields
    try:
        body = exc.response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    files = (detail or {}).get("files") if isinstance(detail, dict) else None
    message = str(fields.get("message") or "")
    if isinstance(files, list) and files:
        listed = [
            f"- {item.get('display_path')} (read_file {item.get('id')})"
            for item in files[:_AMBIGUOUS_LISTED]
            if isinstance(item, dict)
        ]
        more = len(files) - len(listed)
        message += "\n" + "\n".join(listed) + (f"\n+{more} more" if more > 0 else "")
    return _read_error(
        fields.get("error_code"),
        message,
        fields.get("fix") or "Pass one file's id, or a longer path that names one file.",
    )


def _read_error(code: str | None, message: str, fix: str | None = None) -> dict[str, Any]:
    """A read_file failure, sent as ``{status, error_code, message, fix}`` marked isError."""
    return {"status": "error", "error_code": code, "message": message, "fix": fix}


#: Project pages read while resolving a project by name.
_PROJECT_LOOKUP_PAGES = 20
#: Projects this process has resolved: a reference (id, slug or name) to its id.
#: Only hits are kept, so a project created after a miss is found on the next call.
_resolved_projects: dict[str, str] = {}


class ScopeNotFound(ValueError):
    """A ``scope``/``project`` naming no project the caller can read, or several.

    Refused as ``scope_not_found`` (or ``scope_ambiguous``) with the closest
    names, the way the Knowledge door and ``beakr --project`` refuse one. An
    unknown project id used to pass straight through, so documents listed
    nothing ("0 in scope") and an agent read that as an empty project.
    """

    def __init__(self, given: str, *, suggestions: list[str], ambiguous: bool = False) -> None:
        if ambiguous:
            code = "scope_ambiguous"
            message = f"More than one project you can read is named {given!r}."
            fix = f"Pass one of these projects' ids: {'; '.join(suggestions)}."
        else:
            code = "scope_not_found"
            message = f"No project you can read matches {given!r}."
            fix = (
                f"Did you mean: {'; '.join(suggestions)}? Pass its id or exact name."
                if suggestions
                else "Pass a project id or exact name from list_projects."
            )
        super().__init__(f"{message} {fix}")
        #: ``{error_code, message, fix}``, for the compact error result.
        self.fields = {"error_code": code, "message": message, "fix": fix}


async def _readable_projects() -> list[dict[str, Any]]:
    """Every project the caller can read, up to ``_PROJECT_LOOKUP_PAGES`` pages."""
    projects: list[dict[str, Any]] = []
    page_cursor: str | None = None
    for _ in range(_PROJECT_LOOKUP_PAGES):
        page = await call_api(
            lambda sdk, c=page_cursor: sdk.api.projects.list_projects(
                cursor=c, limit=MAX_LIST_ITEMS
            )
        )
        rows, has_more, page_cursor = _page_of(page, "data", "projects")
        projects += rows
        if not has_more:
            break
    return projects


async def _project_id(project: str) -> str:
    """The id of the one readable project ``project`` names (id, slug or name).

    An id is checked too: it must be a project the caller can read. Raises
    :class:`ScopeNotFound` naming the closest projects otherwise.
    """
    from beakr_cli.project_ref import close_matches, find_project

    given = project.strip()
    if given in _resolved_projects:
        return _resolved_projects[given]
    if _is_project_id(given):
        return await _checked_project_id(given)
    projects = await _readable_projects()
    named = [p for p in projects if str(p.get("name") or "").casefold() == given.casefold()]
    if len(named) > 1 and not any(str(p.get("id")) == given for p in projects):
        raise ScopeNotFound(
            given,
            suggestions=[f"{p.get('name')} ({p.get('id')})" for p in named[:5]],
            ambiguous=True,
        )
    found = find_project(projects, given)
    if found is None:
        raise ScopeNotFound(given, suggestions=close_matches(projects, given))
    _resolved_projects[given] = str(found["id"])
    return _resolved_projects[given]


def _is_project_id(value: str) -> bool:
    """Whether ``value`` is a project id (a UUID in its canonical form), not a name."""
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


async def _checked_project_id(project_id: str) -> str:
    """A project id, checked with ``GET /v1/projects/{id}`` instead of the org listing.

    A credential restricted to some projects is refused the listing ("not
    addressed by a single project"), and one without ``projects:read`` is
    refused both routes, so resolving an id through ``GET /v1/projects`` made
    documents and research fail for such a credential even on its own project.
    A 404 still refuses an id that names nothing (the Y12 "0 in scope"); a 403
    passes the id through, and the route it is sent to applies the
    credential's project limits itself.
    """
    try:
        await call_api(lambda sdk: sdk.api.projects.get_project(project_id))
    except ApiCallError as exc:
        status = exc.response.status_code
        if status == 404:
            raise ScopeNotFound(project_id, suggestions=[]) from exc
        if status != 403:
            raise
        return project_id
    _resolved_projects[project_id] = project_id
    return project_id


def _scoped_project(project: str | None = None, project_id: str | None = None) -> str | None:
    """The project filter the non-vocabulary routes still take (argument, else the binding).

    There is no ``personal`` argument, deliberately. Personal is not a special
    scope -- it is a project with ``project_type='personal'`` that
    ``list_projects`` already reports -- so it is named like any other and needs
    no resolution step of its own. The flag used to cost a ``/v1/projects`` round
    trip and a module-level cache to hide it.
    """
    return scope_params(project=project_id or project).get("project_id")


async def _project_by_name_or_id(
    project: str | None = None, project_id: str | None = None
) -> str | None:
    """``_scoped_project`` for a ``project`` that may be a name, as list_projects suggests.

    The research, stats and graph routes take a project id; an agent passes the
    name it read from list_projects (or the ``scope`` it gives knowledge_base) and
    was refused with "project_id must be a valid UUID". A value that names no
    readable project is refused (:class:`ScopeNotFound`), never widened to
    everything the caller can see.
    """
    given = (project_id or project or "").strip()
    if not given:
        return _scoped_project()
    return await _project_id(given)


async def workboard(project: str = "", label_id: str = "") -> dict[str, Any]:
    project, label_id = (project or "").strip(), (label_id or "").strip()
    if not project and not label_id:
        return {"content": "The work board is read for one scope: pass `project` or `label_id`."}
    project_id = None
    if not label_id:
        project_id = await _project_id(project)
    board = (
        await call_api(
            lambda sdk: sdk.knowledge.work_board(project_id=project_id, label_id=label_id or None)
        )
        or {}
    )
    return _work_board(board)


def _work_board(board: dict[str, Any]) -> dict[str, Any]:
    """The live Knowledge-agent plans in one scope, the hosted server's rendering."""
    rows = board.get("work") or []
    if not rows:
        return _capped_result(
            "No Knowledge agent is working in this scope right now.",
            tool="workboard",
            returned=0,
            total=0,
            complete=True,
            hint="",
        )
    blocks = []
    for row in rows[:MAX_LIST_ITEMS]:
        lines = [
            f"- {row.get('agent_kind') or 'agent'} in {row.get('project_name') or 'project'}: "
            f"{row.get('state') or 'planning'}"
        ]
        if row.get("summary"):
            lines.append(f"  plan: {row['summary']}")
        if row.get("write_refs"):
            lines.append(f"  changing: {', '.join(map(str, row['write_refs']))}")
        if row.get("read_refs"):
            lines.append(f"  reading: {', '.join(map(str, row['read_refs']))}")
        if row.get("waiting_on"):
            waits = [
                str(item.get("target")) for item in row["waiting_on"] if isinstance(item, dict)
            ]
            lines.append(f"  waiting on: {', '.join(waits)}")
        blocks.append("\n".join(lines))
    shown = min(len(rows), MAX_LIST_ITEMS)
    return _capped_result(
        "\n".join(blocks),
        tool="workboard",
        returned=shown,
        total=len(rows),
        complete=len(rows) <= MAX_LIST_ITEMS,
        hint=f"Showing {shown} of {len(rows)} agents: pass label_id to read one board.",
    )


#: Listing pages read while looking one integration tool up by name, as the hosted
#: server does. A page holds up to 200 tools; Beakr offers about 150 connector reads.
_INTEGRATION_LOOKUP_PAGES = 5


async def integrations(
    command: Literal["list", "describe", "call"],
    tool: str = "",
    arguments: dict[str, Any] | None = None,
    connector_id: str = "",
    provider: str = "",
    cursor: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    """Read the caller's connected integrations with Beakr's read-only connector tools.

    The hosted server's ``integrations`` tool, through the same routes
    (``GET /v1/integrations/tools``, ``POST /v1/integrations/tools/{tool_name}/call``),
    which decide what a call may reach; the text is the engine's
    ``integration_results`` (copied in :mod:`beakr_cli.mcp_integration_results`).
    """
    args = {
        "command": command,
        "tool": tool,
        "arguments": arguments,
        "connector_id": connector_id,
        "provider": provider,
        "cursor": cursor,
        "limit": limit,
    }
    refused = integration_results.misuse(args)
    if refused is not None:
        return refused
    connector = (connector_id or "").strip() or None
    try:
        if command == "list":
            page = await call_api(
                lambda sdk: sdk.api.integrations.list_integration_tools(
                    provider=(provider or "").strip() or None,
                    connector_id=connector,
                    cursor=_cursor(cursor),
                    limit=_page_size(limit),
                )
            )
            rows, has_more, next_cursor = _page_of(page, "data")
            return _page_result(
                integration_results.tool_list(rows),
                tool="integrations",
                returned=len(rows),
                has_more=has_more,
                next_cursor=next_cursor,
                same_args="and the same command and filters",
            )
        name = tool.strip()
        if command == "describe":
            found = await _integration_tool(name, connector)
            if found is None:
                return integration_results.not_listed(name)
            return integration_results.tool_detail(found)
        data = await call_api(
            lambda sdk: sdk.integrations.call_tool(name, arguments or {}, connector_id=connector)
        )
    except ApiCallError as exc:
        fields = exc.fields
        if fields.get("error_code") == NOT_PROVIDED_CODE:
            # A box without integrations: the server says so and stops listing the tool.
            raise
        return integration_results.refusal(
            fields.get("error_code"),
            str(fields.get("message") or ""),
            fields.get("fix"),
            fields.get("request_id"),
        )
    return integration_results.call_result(dict(data or {}))


async def _integration_tool(name: str, connector_id: str | None) -> dict[str, Any] | None:
    """The listed integration tool named ``name``, or None when no connection has it."""
    page_cursor: str | None = None
    for _ in range(_INTEGRATION_LOOKUP_PAGES):
        page = await call_api(
            lambda sdk, c=page_cursor: sdk.api.integrations.list_integration_tools(
                connector_id=connector_id, cursor=c, limit=200
            )
        )
        rows, has_more, page_cursor = _page_of(page, "data")
        found = integration_results.find(rows, name)
        if found is not None or not has_more:
            return found
    return None


#: The server :func:`discover_tools` describes; set by :func:`register_platform_tools`.
_SERVER: FastMCP | None = None


async def discover_tools(search: str = "") -> str | dict[str, Any]:
    """Describe the discovery-tier tools (W8.2); each is callable by name.

    In the eager listing mode (``BEAKR_MCP_TOOLS=eager``) the tools found are
    added to the listing and the client is told to re-read it. Answers the
    hosted server's shape: one ``- name: description`` line per tool,
    and the tools with their input schemas under ``tools``.
    """
    if _SERVER is None:
        return "Discovery is not available on this server."
    query = (search or "").strip().lower()
    eager = set(tool_surface.eager_tools())
    from mcp.server.fastmcp import FastMCP as _FastMCP

    # The base class lists every registered tool, whatever the listing mode.
    every = await _FastMCP.list_tools(_SERVER)
    found = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema,
            "read_only": bool(tool.annotations and tool.annotations.readOnlyHint),
        }
        for tool in every
        if tool.name not in eager
        and (not query or query in tool.name.lower() or query in (tool.description or "").lower())
    ]
    # In the eager listing mode the tools found join the listing, so a client
    # that calls only listed tools (Claude Code) can call them next.
    reveal = getattr(_SERVER, "reveal", None)
    if reveal is not None:
        await reveal([tool["name"] for tool in found])
    text = "\n".join(f"- {tool['name']}: {tool['description']}" for tool in found)
    return {
        "content": text or "No tool matches that search.",
        "coverage": {"returned": len(found)},
        "tools": found,
    }


PLATFORM_TOOLS = (
    context,
    skills,
    conversations_search,
    import_conversation,
    org_audit_log,
    read_file,
    workboard,
    integrations,
    discover_tools,
)


def register_platform_tools(server: FastMCP) -> None:
    """Register every platform tool on ``server``."""
    global _SERVER
    _SERVER = server
    for tool in PLATFORM_TOOLS:
        # Description and tier come from the shared agent tool surface, the
        # same text the hosted MCP server and Beakr Ask use (W8.4). Every answer
        # is sent as {content, coverage, next} structured content, the hosted
        # server's shape (TieredFastMCP.tool, SPEC-488).
        server.tool(**tool_surface.tool_kwargs(tool.__name__))(tool)
