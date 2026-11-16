"""``--project`` / ``--scope`` values: a project id, name or slug.

People and agents know a project by its name ("Platform Docs") or slug, not
its UUID, and the MCP tools already take a name as ``scope``. Every place the
CLI turns what was typed into a project id resolves it here, with the hosted
server's rule (:func:`pick_project`): the ``--project`` option of every
command, ``BEAKR_PROJECT_ID``, ``beakr projects show|archive|rm``, ``beakr
init --project`` and the stdio MCP tools' ``project`` and ``scope``.

The rule, in order:

1. an id is that project;
2. a name spelled exactly as typed, case included, is that project;
3. otherwise the projects whose name OR slug equals what was typed without
   regard to case: one of them is that project;
4. several is a refusal naming each candidate as ``Name (slug, id)``;
5. none is a refusal naming the closest readable names (the engine's matcher,
   :mod:`beakr_cli.project_scope_names`).

A destructive command (``projects rm``, ``projects archive``) passes
``strict``: a name must be spelled exactly, case included, and name one
project; a match only without regard to case is refused with its candidates,
never acted on. An id or a slug still names one project there.

Slug sits beside the case-insensitive name, after the exact name. It used to
be matched first, so ``test`` chose the project named ``TEST`` (slug ``test``)
over the one named ``test``, and the first case-folded name won silently, so
``TEST`` chose ``test`` and a name two projects share chose whichever was
listed first (sandbox run, 2026-11-14).
"""

from __future__ import annotations

import re
from typing import Any

import typer

from beakr_cli.project_scope_names import closest_project_names
from beakr_cli.terminal_text import visible

#: Project option flags shared by every command; ``--scope`` is the MCP's word.
PROJECT_FLAGS = ("--project", "-P", "--scope")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: Values that are not references: ``auto`` means "the bound or default project".
_PASS_THROUGH = frozenset({"auto"})

_resolved: dict[tuple[str | None, str], str] = {}


def looks_like_id(value: str) -> bool:
    return bool(_UUID.match(value.strip()))


def project_label(project: dict[str, Any]) -> str:
    """``Name (slug, id)``, as the hosted refusal names a candidate."""
    slug = project.get("slug")
    return f"{project.get('name')} ({f'{slug}, ' if slug else ''}{project.get('id')})"


class ProjectNotResolved(ValueError):
    """What was typed names no readable project, or several.

    ``candidates`` are the labels (``Name (slug, id)``) of the projects it
    could mean when ``ambiguous``, and of the closest names otherwise.
    """

    def __init__(
        self, given: str, *, candidates: list[str], ambiguous: bool, same_name: bool = False
    ) -> None:
        self.given = given
        self.candidates = candidates
        self.ambiguous = ambiguous
        if ambiguous and same_name:
            message = (
                f"{len(candidates)} projects are named {given!r}: {'; '.join(candidates)}. "
                "Pass the project's id or slug instead."
            )
        elif ambiguous:
            message = (
                f"More than one project matches {given!r}: {'; '.join(candidates)}. "
                "Pass the project's id or slug instead."
            )
        elif candidates:
            message = (
                f"No project with id, name or slug {given!r}. "
                f"Did you mean: {'; '.join(candidates)}?"
            )
        else:
            message = (
                f"No project with id, name or slug {given!r}. List them with `beakr projects ls`."
            )
        # Project names are what people typed: shown, never obeyed by the terminal.
        super().__init__(visible(message))


def _unique(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for project in projects:
        key = str(project.get("id"))
        if key not in seen:
            seen.add(key)
            out.append(project)
    return out


def closest_projects(projects: list[dict[str, Any]], ref: str) -> list[str]:
    """The labels of the readable projects whose names are closest to ``ref``.

    The engine's matcher and cutoff (``project_scope_names``, a copy of its
    file), so the CLI, the stdio server and the hosted server suggest the same
    names for the same miss.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for project in projects:
        by_name.setdefault(str(project.get("name") or ""), project)
    names = closest_project_names(ref, [name for name in by_name if name])
    return [project_label(by_name[name]) for name in names]


def pick_project(
    projects: list[dict[str, Any]], ref: str, *, strict: bool = False
) -> dict[str, Any]:
    """The one project of ``projects`` that ``ref`` names (the module's rule).

    ``strict`` (a destructive command): a name match counts only when spelled
    exactly and unique; an id or an exact slug still names one project.
    Raises :class:`ProjectNotResolved` when it names none or several.
    """
    ref = ref.strip()
    projects = _unique([p for p in projects if isinstance(p, dict)])
    by_id = [p for p in projects if str(p.get("id") or "").lower() == ref.lower()]
    if ref and by_id:
        return by_id[0]
    exact = [p for p in projects if str(p.get("name") or "") == ref]
    if len(exact) == 1:
        return exact[0]
    folded = ref.casefold()
    if strict and not exact:
        by_slug = [p for p in projects if str(p.get("slug") or "") == ref]
        if len(by_slug) == 1:
            return by_slug[0]
        named = [p for p in projects if folded and str(p.get("name") or "").casefold() == folded]
        if named:
            # Spelled another way than any project's name: never acted on as a guess.
            raise ProjectNotResolved(
                ref,
                candidates=[project_label(p) for p in named],
                ambiguous=True,
                same_name=True,
            )
    loose = exact or [
        p
        for p in projects
        if folded
        and folded in (str(p.get("name") or "").casefold(), str(p.get("slug") or "").casefold())
    ]
    if len(loose) == 1:
        return loose[0]
    if loose:
        same = all(str(p.get("name") or "").casefold() == folded for p in loose)
        raise ProjectNotResolved(
            ref, candidates=[project_label(p) for p in loose], ambiguous=True, same_name=same
        )
    raise ProjectNotResolved(ref, candidates=closest_projects(projects, ref), ambiguous=False)


def find_project(projects: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    """:func:`pick_project`, or None when ``ref`` names no project.

    Several matches still raise :class:`ProjectNotResolved`: an ambiguous
    reference is never answered with one of its candidates.
    """
    try:
        return pick_project(projects, ref)
    except ProjectNotResolved as refused:
        if refused.ambiguous:
            raise
        return None


def same_named(projects: list[dict[str, Any]], ref: str) -> list[dict[str, Any]]:
    """The projects ``ref`` could mean when it names more than one, else none."""
    try:
        pick_project(projects, ref)
    except ProjectNotResolved as refused:
        if refused.ambiguous:
            labels = set(refused.candidates)
            return [p for p in _unique(projects) if project_label(p) in labels]
    return []


def refuse_same_named(
    projects: list[dict[str, Any]],
    ref: str,
    *,
    param_hint: str = "--project",
    strict: bool = False,
) -> None:
    """A usage error naming each candidate when ``ref`` names more than one project.

    Picking one silently read (or archived, or deleted) the wrong project; the
    MCP tools refuse it as ``scope_ambiguous`` and name each. ``strict`` is the
    rule for a destructive command (:func:`pick_project`).
    """
    try:
        pick_project(projects, ref, strict=strict)
    except ProjectNotResolved as refused:
        if refused.ambiguous:
            raise typer.BadParameter(str(refused), param_hint=param_hint) from refused


def resolve_project_ref(ref: str, *, param_hint: str = "--project") -> str:
    """The id of the project ``ref`` names; a usage error naming the candidates if not one."""
    ref = ref.strip()
    if not ref or ref in _PASS_THROUGH or looks_like_id(ref):
        return ref
    from beakr_cli import scope_session

    # A shell session's token reads only its projects, and so does an agent
    # conversation's session: resolved per session.
    session_id = scope_session.token_session_id()
    agent = scope_session.agent_session_id()
    held = session_id or (f"agent:{agent}" if agent else None)
    if (held, ref) in _resolved:
        return _resolved[(held, ref)]
    from beakr_cli.sdk import get_sdk

    with get_sdk() as sdk:
        projects = list(sdk.projects.list() or [])
    try:
        found = pick_project(projects, ref)
    except ProjectNotResolved as refused:
        if not refused.ambiguous and held is not None:
            outside = _outside_session(session_id, ref, agent=agent)
            if outside:
                raise typer.BadParameter(outside, param_hint=param_hint) from refused
        raise typer.BadParameter(str(refused), param_hint=param_hint) from refused
    _resolved[(held, ref)] = str(found["id"])
    return _resolved[(held, ref)]


def _outside_session(session_id: str | None, ref: str, *, agent: str | None) -> str | None:
    """Why ``ref`` did not resolve in this shell's session (or, without one, the agent
    conversation's), when it is a project the person can see that the session does
    not read; None when it is not (or that cannot be told).

    Only the name is resolved with the person's own credential; the command still
    runs in the session. Inside an agent conversation only the person can add a
    project, on the session's page: ``beakr session edit`` is refused there.
    """
    from beakr_sdk.errors import BeakrError

    from beakr_cli import scope_session
    from beakr_cli.sdk import get_sdk

    try:
        session_id = session_id or (scope_session.conversation_session_id(agent) if agent else None)
        if session_id is None:
            return None
        with get_sdk(scoped=False) as person:
            if find_project(list(person.projects.list() or []), ref) is None:
                return None
            session = person.request("GET", f"/v1/sessions/{session_id}")
    except BeakrError:
        return None
    reads = ", ".join(str(p.get("name") or p.get("id")) for p in session.get("read_projects") or [])
    outside = f"{ref!r} isn't one of this session's projects (it reads: {reads or 'none'})."
    picker = session.get("picker_url")
    if agent:
        return f"{outside} Only the person can add it: {picker}"
    return f"{outside} Add it with `beakr session edit {session_id}` or at {picker}."


def project_ref_callback(value: Any) -> Any:
    """Typer callback: resolve a project option (one value or a repeatable list) to ids."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [resolve_project_ref(str(v)) for v in value]
    return resolve_project_ref(str(value))
