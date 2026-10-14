"""Which project a CLI command reads or writes: ``--project``, ``--mine`` or the binding."""

from __future__ import annotations

from typing import Any

# Cache for the personal project id: it never changes for a person.
_personal_project_id: str | None = None


def scope_params(
    *,
    project: str | None = None,
    personal: bool = False,
) -> dict[str, Any]:
    """Build query params for scope.

    Per-call override takes priority over env var and config.
    No scope = org-wide (RLS ensures user only sees what they have access to).
    If personal=True, scopes to the user's personal project.
    """
    params: dict[str, Any] = {}
    if personal and project:
        raise ValueError("Provide at most one of project or personal scope.")
    if personal:
        project_id = get_personal_project_id()
        if not project_id:
            raise ValueError("Could not resolve your personal project.")
        params["project_id"] = project_id
    else:
        from beakr_cli.project_binding import resolve_project

        project_id = resolve_project(project)
        if project_id:
            params["project_id"] = project_id
    return params


def get_personal_project_id() -> str | None:
    """Fetch and cache the user's personal project ID (``None`` if it cannot be read)."""
    global _personal_project_id
    if _personal_project_id is not None:
        return _personal_project_id
    from beakr_cli.sdk import get_sdk

    try:
        with get_sdk() as sdk:
            projects = sdk.projects.list()
    except Exception:
        return None
    for project in projects or []:
        if project.get("project_type") == "personal":
            _personal_project_id = str(project.get("id", ""))
            return _personal_project_id
    return None
