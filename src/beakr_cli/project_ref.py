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

_resolved: dict[str, str] = {}


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

    def __init__(self, given: str, *, candidates: list[str], ambiguous: bool) -> None:
        self.given = given
        self.candidates = candidates
        self.ambiguous = ambiguous
        if ambiguous:
            message = (
                f"More than one project matches {given!r}: {'; '.join(candidates)}. "
                "Pass the id of the one you mean."
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


def pick_project(projects: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    """The one project of ``projects`` that ``ref`` names (the module's rule).

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
    loose = exact or [
        p
        for p in projects
        if folded
        and folded in (str(p.get("name") or "").casefold(), str(p.get("slug") or "").casefold())
    ]
    if len(loose) == 1:
        return loose[0]
    if loose:
        raise ProjectNotResolved(ref, candidates=[project_label(p) for p in loose], ambiguous=True)
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


def resolve_project_ref(ref: str, *, param_hint: str = "--project") -> str:
    """The id of the project ``ref`` names; a usage error naming the candidates if not one."""
    ref = ref.strip()
    if not ref or ref in _PASS_THROUGH or looks_like_id(ref):
        return ref
    if ref in _resolved:
        return _resolved[ref]
    from beakr_cli.sdk import get_sdk

    with get_sdk() as sdk:
        projects = list(sdk.projects.list() or [])
    try:
        found = pick_project(projects, ref)
    except ProjectNotResolved as refused:
        raise typer.BadParameter(str(refused), param_hint=param_hint) from refused
    _resolved[ref] = str(found["id"])
    return _resolved[ref]


def project_ref_callback(value: Any) -> Any:
    """Typer callback: resolve a project option (one value or a repeatable list) to ids."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [resolve_project_ref(str(v)) for v in value]
    return resolve_project_ref(str(value))
