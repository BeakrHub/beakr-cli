"""A project's card types over the documented page-types routes, and a create that chooses them.

The documented operations under ``/v1/knowledge/wiki/page-types`` (list, change,
remove a copy, copy, promote, rename fields, retype cards, settings, history,
suggestions) and ``POST /v1/projects`` with ``card_types``. ``beakr kb types``,
``beakr projects create`` and the stdio MCP tools ``edit_card_type``,
``card_type_settings``, ``update_existing_cards`` and ``create_project`` call
them.

They are called through the SDK's ``request`` so the commands and tools work
with the released beakr-sdk the CLI pins, which predates its typed methods for
them (``knowledge.list_card_types``, ``update_card_type``,
``remove_card_type_copy``, ``copy_card_types``, ``promote_card_types``,
``rename_card_type_fields``, ``retype_cards``, ``set_card_type_settings``,
``card_type_history``, ``card_type_suggestions``,
``dismiss_card_type_suggestion`` and ``projects.create(card_types=...)`` in
beakr-python). Each function takes the same arguments as that method and sends
the same request, so a call site moves back to the SDK by name once a release
carries them (ENG-2524).

Every function works with the sync client and the async one; from the async one
it returns an awaitable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._paths import segment

PAGE_TYPES = "/v1/knowledge/wiki/page-types"


def _type_path(key: str) -> str:
    """One card type's route; the key is one percent-encoded path segment."""
    return f"{PAGE_TYPES}/{segment(key)}"


def list_card_types(
    sdk: Any,
    *,
    project_id: str | None = None,
    include_proposed: bool | None = None,
    include_deprecated: bool | None = None,
    include_counts: bool | None = None,
) -> Any:
    """The card types a project uses: ``{page_types, map_mode, settings, kept_types}``.

    ``include_counts`` adds ``card_count`` to each type and ``unlisted_types``.
    """
    return sdk.request(
        "GET",
        PAGE_TYPES,
        params={
            "project_id": project_id,
            "include_proposed": include_proposed,
            "include_deprecated": include_deprecated,
            "include_counts": include_counts,
        },
    )


def update_card_type(
    sdk: Any,
    key: str,
    *,
    scope: str = "project",
    project_id: str | None = None,
    label: str | None = None,
    description: str | None = None,
    when_to_use: str | None = None,
    when_not_to_use: str | None = None,
    aliases: list[str] | None = None,
    fields: list[dict[str, Any]] | None = None,
    grain: str | None = None,
    status: str | None = None,
) -> Any:
    """Change a card type as one scope holds it. Returns ``{page_type}``.

    A field left out is kept; a list given replaces that list.
    """
    body = clean_body(
        {
            "scope": scope,
            "project_id": project_id,
            "label": label,
            "description": description,
            "when_to_use": when_to_use,
            "when_not_to_use": when_not_to_use,
            "aliases": aliases,
            "fields": fields,
            "grain": grain,
            "status": status,
        }
    )
    return sdk.request("PATCH", _type_path(key), json=body)


def remove_card_type_copy(
    sdk: Any, key: str, *, scope: str = "project", project_id: str | None = None
) -> Any:
    """Remove a scope's own copy of a card type. Returns ``{removed, key, scope}``."""
    return sdk.request("DELETE", _type_path(key), params={"scope": scope, "project_id": project_id})


def copy_card_types(
    sdk: Any,
    from_project_id: str,
    to_project_id: str,
    keys: list[str],
    *,
    replace: bool = False,
) -> Any:
    """Copy card types one project defines into another. Returns ``{copied, replaced, skipped}``."""
    body = {
        "from_project_id": from_project_id,
        "to_project_id": to_project_id,
        "keys": list(keys),
        "replace": replace,
    }
    return sdk.request("POST", f"{PAGE_TYPES}/copy", json=body)


def promote_card_types(sdk: Any, project_id: str, keys: list[str], *, replace: bool = False) -> Any:
    """Copy card types a project defines up to the organization (the route names it ``source``)."""
    body = {"source": project_id, "keys": list(keys), "replace": replace}
    return sdk.request("POST", f"{PAGE_TYPES}/promote", json=body)


def rename_card_type_fields(
    sdk: Any,
    key: str,
    project_id: str,
    renames: Mapping[str, str] | list[dict[str, str]],
    *,
    dry_run: bool = True,
    continue_event_id: str | None = None,
) -> Any:
    """Rename fields on every card of a type in one project. A dry run unless asked.

    ``renames`` is ``{old: new}`` or the route's own list of ``{"old", "new"}``.
    """
    pairs = (
        [{"old": old, "new": new} for old, new in renames.items()]
        if isinstance(renames, Mapping)
        else [dict(pair) for pair in renames]
    )
    body = clean_body(
        {
            "project_id": project_id,
            "renames": pairs,
            "dry_run": dry_run,
            "continue_event_id": continue_event_id,
        }
    )
    return sdk.request("POST", f"{_type_path(key)}/rename-fields", json=body)


def retype_cards(
    sdk: Any,
    key: str,
    project_id: str,
    to_type: str,
    *,
    dry_run: bool = True,
    suggestion_id: str | None = None,
    continue_event_id: str | None = None,
) -> Any:
    """Move every card of a type in one project to another type. A dry run unless asked."""
    body = clean_body(
        {
            "project_id": project_id,
            "to_type": to_type,
            "dry_run": dry_run,
            "suggestion_id": suggestion_id,
            "continue_event_id": continue_event_id,
        }
    )
    return sdk.request("POST", f"{_type_path(key)}/retype-cards", json=body)


def set_card_type_settings(
    sdk: Any,
    project_id: str,
    *,
    inherit: str | None = None,
    agent_may_change: bool | None = None,
    keep_types_in_use: bool = False,
    dry_run: bool = True,
) -> Any:
    """Change where a project's card types come from, or who may change them.

    A dry run unless ``dry_run=False``.
    """
    body = clean_body(
        {
            "project_id": project_id,
            "inherit": inherit,
            "agent_may_change": agent_may_change,
            "keep_types_in_use": keep_types_in_use,
            "dry_run": dry_run,
        }
    )
    return sdk.request("PUT", f"{PAGE_TYPES}/settings", json=body)


def card_type_history(
    sdk: Any,
    *,
    project_id: str | None = None,
    scope: str | None = None,
    key: str | None = None,
    include_suggestions: bool | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> Any:
    """One page of the changes to card types, newest first: ``{data, next_cursor, has_more}``."""
    return sdk.request(
        "GET",
        f"{PAGE_TYPES}/history",
        params={
            "project_id": project_id,
            "scope": scope,
            "key": key,
            "include_suggestions": include_suggestions,
            "limit": limit,
            "cursor": cursor,
        },
    )


def card_type_suggestions(
    sdk: Any, project_id: str, *, limit: int | None = None, cursor: str | None = None
) -> Any:
    """One page of what waits for a person in a project: ``{data, next_cursor, has_more}``."""
    return sdk.request(
        "GET",
        f"{PAGE_TYPES}/suggestions",
        params={"project_id": project_id, "limit": limit, "cursor": cursor},
    )


def dismiss_card_type_suggestion(sdk: Any, suggestion_id: str, project_id: str) -> Any:
    """Dismiss one suggestion. Returns ``{suggestion, activity_event_id}``."""
    return sdk.request(
        "POST",
        f"{PAGE_TYPES}/suggestions/{segment(suggestion_id)}/dismiss",
        json={"project_id": project_id},
    )


def create_project(
    sdk: Any,
    name: str,
    *,
    description: str | None = None,
    tags: list[str] | None = None,
    card_types: dict[str, Any] | None = None,
) -> Any:
    """Create a project, choosing where its card types come from (``card_types``).

    The same keyed create as ``projects.create``: ``POST /v1/projects`` is one
    of the creates the engine replays on a resend with the same
    ``Idempotency-Key``, so a retry after a lost answer does not make a second
    project.
    """
    body = clean_body(
        {"name": name, "description": description, "tags": tags, "card_types": card_types}
    )
    return sdk.request("POST", "/v1/projects", json=body)
