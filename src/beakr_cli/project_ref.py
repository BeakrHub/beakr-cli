"""``--project`` / ``--scope`` values: a project id, slug or exact name.

People and agents know a project by its name ("Platform Docs") or slug, not
its UUID, and the MCP tools already take a name as ``scope``. Every command's
project option resolves its value here, once per process: an id passes through
without a request, anything else is matched against the projects the caller
can see (id, then slug, then case-insensitive name). A miss is a usage error
that lists the closest names, so the next attempt is right.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

import typer

#: Project option flags shared by every command; ``--scope`` is the MCP's word.
PROJECT_FLAGS = ("--project", "-P", "--scope")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: Values that are not references: ``auto`` means "the bound or default project".
_PASS_THROUGH = frozenset({"auto"})

_resolved: dict[str, str] = {}


def looks_like_id(value: str) -> bool:
    return bool(_UUID.match(value.strip()))


def find_project(projects: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    """Match by id, then slug, then case-insensitive name."""
    for key in ("id", "slug"):
        for project in projects:
            if str(project.get(key) or "") == ref:
                return project
    lowered = ref.casefold()
    for project in projects:
        if str(project.get("name") or "").casefold() == lowered:
            return project
    return None


def close_matches(projects: list[dict[str, Any]], ref: str, limit: int = 3) -> list[str]:
    """``Name (slug, id)`` for the projects whose name or slug is nearest ``ref``."""
    by_word: dict[str, dict[str, Any]] = {}
    for project in projects:
        for key in ("name", "slug"):
            word = str(project.get(key) or "")
            if word:
                by_word.setdefault(word.casefold(), project)
    words = difflib.get_close_matches(ref.casefold(), list(by_word), n=limit * 2, cutoff=0.4)
    words += [w for w in by_word if ref.casefold() in w and w not in words]
    seen: list[str] = []
    out: list[str] = []
    for word in words:
        project = by_word[word]
        pid = str(project.get("id"))
        if pid in seen:
            continue
        seen.append(pid)
        slug = project.get("slug")
        out.append(f"{project.get('name')} ({f'{slug}, ' if slug else ''}{pid})")
        if len(out) == limit:
            break
    return out


def resolve_project_ref(ref: str, *, param_hint: str = "--project") -> str:
    """The id of the project ``ref`` names; a usage error naming close matches if none."""
    ref = ref.strip()
    if not ref or ref in _PASS_THROUGH or looks_like_id(ref):
        return ref
    if ref in _resolved:
        return _resolved[ref]
    from beakr_cli.sdk import get_sdk

    with get_sdk() as sdk:
        projects = list(sdk.projects.list() or [])
    found = find_project(projects, ref)
    if found is None:
        suggestions = close_matches(projects, ref)
        hint = (
            f" Did you mean: {'; '.join(suggestions)}?"
            if suggestions
            else " List them with `beakr projects ls`."
        )
        raise typer.BadParameter(
            f"No project with id, slug or name {ref!r}.{hint}", param_hint=param_hint
        )
    _resolved[ref] = str(found["id"])
    return _resolved[ref]


def project_ref_callback(value: Any) -> Any:
    """Typer callback: resolve a project option (one value or a repeatable list) to ids."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [resolve_project_ref(str(v)) for v in value]
    return resolve_project_ref(str(value))
