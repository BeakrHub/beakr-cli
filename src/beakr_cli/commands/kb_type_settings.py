"""``beakr kb types settings|history|suggestions|dismiss-suggestion``.

A project chooses where its card types come from (``inherit``: the
organization's and the built-in ones, the built-in ones only, or only its own)
and whether Beakr itself may add or change them. ``settings`` shows the two
settings, or changes them: a change previews and writes nothing unless given
``--apply``, listing the types the project would stop being offered with their
card counts. ``history`` lists the changes made to a project's types (or the
organization's). ``suggestions`` lists what waits for a person: types Beakr was
not allowed to add and cards to move; ``dismiss-suggestion`` dismisses one.

Over ``/v1/knowledge/wiki/page-types`` (whose listing answers the settings),
``.../settings``, ``.../history`` and ``.../suggestions`` through
:mod:`beakr_cli.card_type_routes` (``list_card_types``,
``set_card_type_settings``, ``card_type_history``, ``card_type_suggestions``
and ``dismiss_card_type_suggestion``: the SDK's ``request``, since the released
beakr-sdk has no typed methods for these routes). The commands attach to the
``kb types`` group of :mod:`beakr_cli.commands.kb_types`.
"""

from __future__ import annotations

from typing import Any

import typer

from beakr_cli import card_settings, card_type_routes, card_types
from beakr_cli.commands.kb_types import (
    _apply_opt,
    _json_opt,
    _org_wide_opt,
    _project_opt,
    _settings_text,
    _usage,
)
from beakr_cli.commands.kb_types import app as types_app
from beakr_cli.commands.project_knowledge import _require_project
from beakr_cli.output import console, emit, err_console, print_table
from beakr_cli.sdk import get_sdk

_limit_opt = typer.Option(50, "--limit", "-n", min=1, max=200, help="Entries a page.")
_cursor_opt = typer.Option(None, "--cursor", help="Continue from a previous page's cursor.")


def _settings_lines(settings: dict[str, Any]) -> list[str]:
    return [
        f"inherit\t{settings.get('inherit')}",
        f"agent_may_change\t{str(bool(settings.get('agent_may_change'))).lower()}",
    ]


def settings_command(
    project: str | None = _project_opt,
    inherit: str | None = typer.Option(
        None,
        "--inherit",
        help="Which card types the project is offered: 'organization' (the organization's "
        "and the built-in ones), 'builtin' (the built-in ones only) or 'own' (only the "
        "types the project defines; it needs at least one).",
    ),
    beakr_may_add: bool | None = typer.Option(
        None,
        "--beakr-may-add/--no-beakr-may-add",
        help="Whether Beakr itself may add and change the project's card types; with "
        "--no-beakr-may-add only people do, and a type Beakr would have added is kept "
        "as a suggestion.",
    ),
    keep_types_in_use: bool = typer.Option(
        False,
        "--keep-types-in-use",
        help="Make every type that would drop out and still has cards one of the "
        "project's own types.",
    ),
    apply: bool = _apply_opt,
    json_out: bool = _json_opt,
) -> None:
    """Show a project's card type settings, or change them. A change previews unless --apply.

    With no setting given it shows the two settings. With --inherit or
    --beakr-may-add/--no-beakr-may-add it previews the change: which types the
    project would stop being offered and how many cards each has. --apply makes
    it. Applying never changes or removes a card or a type; a dropped type that
    still has cards gets a suggestion to move them (`beakr kb types
    suggestions`). Only the project's owners may change the settings.
    """
    if inherit is not None and inherit not in card_settings.INHERIT_VALUES:
        _usage("--inherit is 'organization', 'builtin' or 'own'.")
    changing = inherit is not None or beakr_may_add is not None
    if not changing and (keep_types_in_use or apply):
        _usage(
            "Say which setting to change: --inherit organization|builtin|own, "
            "--beakr-may-add or --no-beakr-may-add."
        )
    project_id = _require_project(project)
    with get_sdk() as sdk:
        if not changing:
            # The listing answers the settings beside the types.
            listing = card_type_routes.list_card_types(sdk, project_id=project_id) or {}
            settings = dict(listing.get("settings") or {})
        else:
            result = dict(
                card_type_routes.set_card_type_settings(
                    sdk,
                    project_id,
                    inherit=inherit,
                    agent_may_change=beakr_may_add,
                    keep_types_in_use=keep_types_in_use,
                    dry_run=not apply,
                )
            )
    if not changing:
        emit(
            settings,
            json_out=json_out,
            render=lambda s: console.print(_settings_text(s), highlight=False, markup=False),
            lines=_settings_lines,
        )
        return
    text = card_settings.settings_text(result, card_settings.COMMAND_WORDS)
    emit(
        result,
        json_out=json_out,
        render=lambda _result: console.print(text, highlight=False, markup=False),
        lines=lambda _result: text.splitlines(),
    )


def _who(entry: dict[str, Any]) -> str:
    if entry.get("actor_type") == "agent":
        return str(entry.get("actor_name") or "Beakr")
    return str(entry.get("actor_user_id") or entry.get("actor_name") or "")


def _entry_line(entry: dict[str, Any]) -> str:
    parts = [
        str(entry.get("id")),
        str(entry.get("created_at") or ""),
        str(entry.get("kind")),
        str(entry.get("key") or ""),
        str(entry.get("title") or ""),
    ]
    if entry.get("state"):
        parts.append(f"state={entry['state']}")
    return "\t".join(parts)


def _page_lines(empty: str) -> Any:
    def lines(page: dict[str, Any]) -> list[str]:
        return [_entry_line(entry) for entry in page.get("data") or []] or [empty]

    return lines


def _render_history(page: dict[str, Any]) -> None:
    entries = page.get("data") or []
    if not entries:
        console.print("No changes to card types yet.", highlight=False, markup=False)
        return
    print_table(
        ["When", "Who", "Kind", "Type", "What", "Id"],
        [
            [
                str(entry.get("created_at") or ""),
                _who(entry),
                str(entry.get("kind")) + (f" ({entry['state']})" if entry.get("state") else ""),
                str(entry.get("key") or ""),
                str(entry.get("title") or ""),
                str(entry.get("id")),
            ]
            for entry in entries
        ],
        title="Card type history",
        whole=("Id",),
    )
    if page.get("has_more") and page.get("next_cursor"):
        err_console.print(
            f"More: pass --cursor {page['next_cursor']}", markup=False, highlight=False
        )


def history_command(
    project: str | None = _project_opt,
    org_wide: bool = _org_wide_opt,
    key: str | None = typer.Option(None, "--key", "-k", help="Only this type's changes."),
    suggestions: bool = typer.Option(
        False, "--suggestions", help="Add suggestions, each with its state."
    ),
    limit: int = _limit_opt,
    cursor: str | None = _cursor_opt,
    json_out: bool = _json_opt,
) -> None:
    """The changes made to a project's card types, newest first (--org-wide: the organization's).

    Each entry says when, who, what kind of change and one readable line; --json
    adds the fields that changed and the definition before and after.
    """
    if org_wide and project:
        _usage(f"{card_types.ORG_WITH_PROJECT[0]} Leave --project out with --org-wide.")
    where: dict[str, Any] = (
        {"scope": "org"} if org_wide else {"project_id": _require_project(project)}
    )
    with get_sdk() as sdk:
        page = dict(
            card_type_routes.card_type_history(
                sdk,
                **where,
                key=key,
                include_suggestions=suggestions or None,
                limit=limit,
                cursor=cursor,
            )
        )
    emit(
        page,
        json_out=json_out,
        render=_render_history,
        lines=_page_lines("(no changes to card types)"),
    )


def _answer_hint(entry: dict[str, Any], project_id: str) -> str:
    """How a person answers one suggestion."""
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    if entry.get("kind") == "retype_suggested" and details.get("from_type"):
        return (
            f"beakr kb types retype '{details['from_type']}' -P {project_id} --to TYPE "
            f"--suggestion {entry.get('id')}"
        )
    return f"beakr kb types dismiss-suggestion {entry.get('id')} -P {project_id}"


def suggestions_command(
    project: str | None = _project_opt,
    limit: int = _limit_opt,
    cursor: str | None = _cursor_opt,
    json_out: bool = _json_opt,
) -> None:
    """What waits for a person in a project: types Beakr held back, and cards to move.

    A `held_back` entry is a type Beakr was not allowed to add; add it with
    `beakr kb types edit` or dismiss it. A `retype_suggested` entry names a type
    whose cards should move: answer it with `beakr kb types retype ...
    --suggestion ID`, or dismiss it with `beakr kb types dismiss-suggestion`.
    """
    project_id = _require_project(project)
    with get_sdk() as sdk:
        page = dict(
            card_type_routes.card_type_suggestions(sdk, project_id, limit=limit, cursor=cursor)
        )

    def render(data: dict[str, Any]) -> None:
        entries = data.get("data") or []
        if not entries:
            console.print("No suggestions wait for a person.", highlight=False, markup=False)
            return
        for entry in entries:
            console.print(
                f"{entry.get('kind')}: {entry.get('title')} (id: {entry.get('id')})",
                highlight=False,
                markup=False,
            )
            console.print(f"  {_answer_hint(entry, project_id)}", highlight=False, markup=False)
        if data.get("has_more") and data.get("next_cursor"):
            err_console.print(
                f"More: pass --cursor {data['next_cursor']}", markup=False, highlight=False
            )

    emit(page, json_out=json_out, render=render, lines=_page_lines("(no suggestions)"))


def dismiss_suggestion_command(
    suggestion_id: str = typer.Argument(..., help="The id from `beakr kb types suggestions`."),
    project: str | None = _project_opt,
    json_out: bool = _json_opt,
) -> None:
    """Dismiss one suggestion. Only the project's owners may; exit 5 if it was already answered."""
    project_id = _require_project(project)
    with get_sdk() as sdk:
        dismissed = dict(
            card_type_routes.dismiss_card_type_suggestion(sdk, suggestion_id, project_id)
        )
    emit(
        dismissed,
        json_out=json_out,
        render=lambda d: err_console.print(
            "Dismissed the suggestion: "
            f"{(d.get('suggestion') or {}).get('title') or suggestion_id}",
            markup=False,
            highlight=False,
        ),
        json_when_piped=False,
    )


def register(root: typer.Typer) -> None:
    """Attach the commands to ``kb types`` (the root is unused)."""
    types_app.command("settings")(settings_command)
    types_app.command("history")(history_command)
    types_app.command("suggestions")(suggestions_command)
    types_app.command("dismiss-suggestion")(dismiss_suggestion_command)
