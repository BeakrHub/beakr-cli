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

import re
from typing import TYPE_CHECKING, Any

from beakr_cli import mcp_result, tool_surface
from beakr_cli.mcp_errors import ApiCallError, call_api

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
        project: Project ID to focus on. Omit for org-wide context.
        question: The task or question, to rank what is included.
        format: markdown (default), claude_md, agents_md or json.
        budget_chars: Approximate size of the result (max 20000).
    """
    if format not in _CONTEXT_FORMATS:
        return f"Invalid format '{format}'. Use one of: {', '.join(_CONTEXT_FORMATS)}."
    data = await call_api(
        lambda sdk: sdk.engine.context(
            project_id=project or None,
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
            lambda sdk: sdk.private_api.skills.list_library_skills(
                cursor=_cursor(cursor), limit=_page_size(limit)
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
            lambda sdk, c=page_cursor: sdk.private_api.skills.list_library_skills(
                cursor=c, limit=MAX_LIST_ITEMS
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
        project: Project ID to restrict to.
        limit: Conversations per page (1-25 with a query, 1-50 without).
        cursor: The next_cursor from the previous page's coverage (same query and filters).
    """
    if query and not client:
        return await _search_index(query, project=project, limit=limit, cursor=cursor)
    page_size = _page_size(limit, default=10)
    page = await call_api(
        lambda sdk: sdk.api.conversations.list_conversations(
            client=client or None,
            project_id=project or None,
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


async def _search_index(query: str, *, project: str, limit: int, cursor: str) -> dict[str, Any]:
    page = await call_api(
        lambda sdk: sdk.api.conversations.search(
            q=query,
            project_id=project or None,
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
        project: Project ID it belongs to.
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
    if project:
        body["project_id"] = project
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


_UUID_TEXT = re.compile(r"^[0-9a-fA-F-]{36}$")
#: Characters of a file returned per read_file call.
READ_FILE_CHARS = 20_000
#: Candidate files an ambiguous path's error lists.
_AMBIGUOUS_LISTED = 10


async def read_file(
    artifact_id: str = "", path: str = "", offset: int = 0, limit: int = READ_FILE_CHARS
) -> str | dict[str, Any]:
    """A window of a document's extracted text, as the hosted read_file reads it.

    ``artifact_id`` (or ``path``) may be a file id, a version id, a plain
    ``citation_key`` from Sources, a citation's readable reference or a file's
    path as a read prints it. The citation key pins the exact cited version;
    a short readable reference or file id may follow the current version after
    a file changes. With `` › <unit>`` (or ``#<unit>``) after one
    (``memo.pdf › p. 7``) the window is the text around that place. One route resolves and reads all
    of them (``POST /v1/artifacts/read``), so a reference that reaches no
    readable file is one answer whatever the reason.
    """
    reference = str(path or artifact_id or "").strip()
    if not reference:
        return _read_error(
            "invalid_arguments",
            "Pass `artifact_id`: a citation_key from Sources (exact cited version), a file "
            "id, a citation's reference (memo.pdf › p. 7), or a file's path (optionally with "
            "' › <unit>').",
            "Copy the citation_key, `read_file <id>`, reference or path from a Knowledge "
            "result's Sources.",
        )
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or READ_FILE_CHARS), READ_FILE_CHARS))
    try:
        data = await call_api(lambda sdk: sdk.files.read(reference, offset=offset, max_chars=limit))
    except ApiCallError as exc:
        status = exc.response.status_code
        if status == 404:
            # The hosted read_file's error exactly (beakr/api/mcp_remote/tools.py).
            return _read_error(
                None,
                "No readable file matches this reference. Pass `artifact_id`: the citation_key, "
                "file id, reference or path from a Knowledge result's Sources (location.file_id, "
                "or `read_file <id>`).",
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


async def _project_id(project: str) -> str | None:
    if _UUID_TEXT.match(project):
        return project
    matches: list[dict[str, Any]] = []
    page_cursor: str | None = None
    for _ in range(_PROJECT_LOOKUP_PAGES):
        page = await call_api(
            lambda sdk, c=page_cursor: sdk.api.projects.list_projects(
                cursor=c, limit=MAX_LIST_ITEMS
            )
        )
        rows, has_more, page_cursor = _page_of(page, "data", "projects")
        matches += [p for p in rows if str(p.get("name", "")).lower() == project.lower()]
        if not has_more:
            break
    return str(matches[0]["id"]) if len(matches) == 1 else None


async def workboard(project: str = "", label_id: str = "") -> dict[str, Any]:
    project, label_id = (project or "").strip(), (label_id or "").strip()
    if not project and not label_id:
        return {"content": "The work board is read for one scope: pass `project` or `label_id`."}
    project_id = None
    if not label_id:
        project_id = await _project_id(project)
        if project_id is None:
            return {
                "content": f"No single project named {project!r}; pass a project id "
                "(list_projects shows them)."
            }
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


#: The server :func:`discover_tools` describes; set by :func:`register_platform_tools`.
_SERVER: FastMCP | None = None


async def discover_tools(search: str = "") -> str | dict[str, Any]:
    """Describe the tools not listed by default (W8.2); each is callable by name.

    Answers the hosted server's shape: one ``- name: description`` line per tool,
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
