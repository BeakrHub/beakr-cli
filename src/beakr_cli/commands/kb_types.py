"""``beakr kb types ls|edit|reset|copy|promote|rename-fields|retype``: a project's card types.

Card types are the kinds of cards Knowledge writes for a project (its
ontology), over ``/v1/knowledge/wiki/page-types`` through the SDK's
``knowledge.list_card_types``, ``update_card_type``, ``remove_card_type_copy``,
``copy_card_types`` and ``promote_card_types``. ``ls`` shows the types one
project uses and which scope defines each. ``edit`` changes a type as one scope
holds it: a project (the default) or, with ``--org-wide``, the organization;
changing a type the scope only inherits gives the scope its own copy, and
``reset`` removes that copy. ``copy`` carries the types one project defines
into another project, and ``promote`` up to the organization. A project's
owners change its types and an organization's admins the organization's. Cards
already written are not rewritten; later compiles use the new definition.

``rename-fields`` and ``retype`` are the two that do change the cards a project
already has (``knowledge.rename_card_type_fields``, ``retype_cards``): the first
renames fields on every card of a type, the second moves every card of a type
to another type. Both preview and write nothing unless given ``--apply``.
"""

from __future__ import annotations

from typing import Any

import typer

from beakr_cli import card_settings, card_types, card_updates
from beakr_cli.commands.kb import app as kb_app
from beakr_cli.commands.project_knowledge import _require_project
from beakr_cli.output import console, emit, err_console, print_table
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="A project's card types: the kinds of cards Knowledge writes for it.")

# Every kb command spells the project -P, --project, --scope or -s, so the API's
# `scope` (whose copy of the type changes) is --org-wide here, not --scope.
_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    "-s",
    help="Project id, slug or name (default: the bound or configured project).",
    callback=project_ref_callback,
)
_org_wide_opt = typer.Option(
    False,
    "--org-wide",
    help="The organization's type, for every project without its own copy, instead of "
    "one project's. Needs an organization admin; takes no --project.",
)
_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")

_SCOPE_NAMES = {"project": "project", "org": "organization", "global": "built-in"}
_COPY_OF = {"global": "copy of built-in", "org": "copy of organization type"}


def _usage(message: str) -> None:
    err_console.print(message, markup=False, highlight=False)
    raise typer.Exit(2)


def _schema(page_type: dict[str, Any]) -> dict[str, Any]:
    schema = page_type.get("metadata_schema")
    return schema if isinstance(schema, dict) else {}


def _scope_text(page_type: dict[str, Any]) -> str:
    """``project (copy of built-in)``, ``organization``, ``built-in``."""
    scope = str(page_type.get("scope") or "")
    text = _SCOPE_NAMES.get(scope, scope)
    copy_of = _COPY_OF.get(str(page_type.get("overrides") or ""))
    return f"{text} ({copy_of})" if copy_of else text


def _fields_text(page_type: dict[str, Any]) -> str:
    schema = _schema(page_type)
    required = {str(name).casefold() for name in schema.get("required_attributes") or []}
    return "; ".join(
        f"{name} (required)" if str(name).casefold() in required else str(name)
        for name in schema.get("key_attributes") or []
    )


def _line(page_type: dict[str, Any]) -> str:
    parts = [
        str(page_type.get("key")),
        str(page_type.get("label") or ""),
        f"scope={_scope_text(page_type)}",
        f"status={page_type.get('status')}",
    ]
    if page_type.get("card_count") is not None:
        parts.append(f"cards={page_type['card_count']}")
    if _schema(page_type).get("grain"):
        parts.append(f"grain={_schema(page_type)['grain']}")
    if _fields_text(page_type):
        parts.append(f"fields={_fields_text(page_type)}")
    return "\t".join(parts)


def _settings_text(settings: dict[str, Any]) -> str:
    """The setting in force, as `beakr kb types settings` words it."""
    inherit = str(settings.get("inherit"))
    who = (
        "Beakr may add and change its card types."
        if settings.get("agent_may_change")
        else "Only people add and change its card types."
    )
    return f"This project is offered {card_settings.SOURCES.get(inherit, inherit)}. {who}"


def _list_lines(listing: dict[str, Any]) -> list[str]:
    """One line a type, then the cards of unlisted types and the setting in force."""
    lines = [_line(page_type) for page_type in listing.get("page_types") or []]
    lines = lines or ["(no card types)"]
    lines += [
        f"unlisted\t{unlisted.get('key')}\tcards={unlisted.get('card_count')}"
        for unlisted in listing.get("unlisted_types") or []
    ]
    settings = listing.get("settings")
    if isinstance(settings, dict):
        lines.append(
            f"settings\tinherit={settings.get('inherit')}"
            f"\tagent_may_change={str(bool(settings.get('agent_may_change'))).lower()}"
        )
    return lines


def _render_list(listing: dict[str, Any]) -> None:
    types = listing.get("page_types") or []
    counted = any(page_type.get("card_count") is not None for page_type in types)
    print_table(
        ["Key", "Label", "Scope", "Status", "Grain", *(["Cards"] if counted else []), "Fields"],
        [
            [
                str(page_type.get("key")),
                str(page_type.get("label") or ""),
                _scope_text(page_type),
                str(page_type.get("status") or ""),
                str(_schema(page_type).get("grain") or ""),
                *([str(page_type.get("card_count") or 0)] if counted else []),
                _fields_text(page_type),
            ]
            for page_type in types
        ],
        title="Card types",
        whole=("Key",),
    )
    unlisted = listing.get("unlisted_types") or []
    if unlisted:
        console.print(
            "Cards carry these types, which are not offered here: "
            + "; ".join(f"{u.get('key')} ({u.get('card_count')})" for u in unlisted),
            highlight=False,
            markup=False,
        )
    if isinstance(listing.get("settings"), dict):
        console.print(_settings_text(listing["settings"]), highlight=False, markup=False)


def _render_type(saved: dict[str, Any]) -> None:
    page_type = saved.get("page_type") or {}
    rows = [
        ("Key", page_type.get("key")),
        ("Label", page_type.get("label")),
        ("Scope", _scope_text(page_type)),
        ("Status", page_type.get("status")),
        ("Grain", _schema(page_type).get("grain")),
        ("Description", page_type.get("description")),
        ("When to use", page_type.get("when_to_use")),
        ("When not to use", page_type.get("when_not_to_use")),
        ("Aliases", ", ".join(map(str, page_type.get("aliases") or []))),
        ("Fields", _fields_text(page_type)),
    ]
    for label, value in rows:
        if value:
            console.print(f"{label}: {value}", highlight=False, markup=False)


def _where(key: str, project: str | None, org_wide: bool) -> tuple[str, dict[str, Any]]:
    """The checked key and ``{scope, project_id}`` for a change or a reset."""
    key = key.strip()
    if not card_types.valid_type_key(key):
        _usage(
            f"{card_types.BAD_TYPE_KEY[0]} Pass the key exactly as `beakr kb types ls` prints it."
        )
    if org_wide:
        if project:
            _usage(f"{card_types.ORG_WITH_PROJECT[0]} Leave --project out with --org-wide.")
        return key, {"scope": "org", "project_id": None}
    return key, {"scope": "project", "project_id": _require_project(project)}


def _field_list(names: list[str], required: list[str]) -> list[dict[str, Any]]:
    """--field names in the order given; a --required-field marks its name required.

    A required name that no --field gives is added after them, in the order
    given, so ``--required-field`` alone is enough for a list of required fields.
    """
    must = {name.strip().casefold() for name in required if name.strip()}
    fields = [
        {"name": name.strip(), "required": name.strip().casefold() in must}
        for name in names
        if name.strip()
    ]
    listed = {field["name"].casefold() for field in fields}
    for name in required:
        if name.strip() and name.strip().casefold() not in listed:
            fields.append({"name": name.strip(), "required": True})
            listed.add(name.strip().casefold())
    return fields


@app.command("ls")
def ls_command(
    project: str | None = _project_opt,
    counts: bool = typer.Option(
        False,
        "--counts",
        "-c",
        help="Add each type's live cards, and the types cards carry that are not offered.",
    ),
    json_out: bool = _json_opt,
) -> None:
    """The card types a project uses, which scope defines each, and the setting in force."""
    project_id = _require_project(project)
    with get_sdk() as sdk:
        listing = dict(
            sdk.knowledge.list_card_types(project_id=project_id, include_counts=counts or None)
        )
    emit(listing, json_out=json_out, render=_render_list, lines=_list_lines)


@app.command("edit")
def edit_command(
    key: str = typer.Argument(..., help="The type's key, from `beakr kb types ls`."),
    project: str | None = _project_opt,
    org_wide: bool = _org_wide_opt,
    label: str | None = typer.Option(None, "--label", help="The display name."),
    description: str | None = typer.Option(
        None, "--description", "-d", help="What a card of this type is about."
    ),
    when_to_use: str | None = typer.Option(
        None, "--when-to-use", help="When a thing in a source should become a card of this type."
    ),
    when_not_to_use: str | None = typer.Option(
        None,
        "--when-not-to-use",
        help="What looks like this type but belongs to another, and which.",
    ),
    alias: list[str] = typer.Option(  # noqa: B008
        [], "--alias", help="Another name people use for this type; repeatable, replaces the list."
    ),
    field: list[str] = typer.Option(  # noqa: B008
        [],
        "--field",
        help="A field of the type; repeatable. The fields given, in order, replace the list.",
    ),
    required_field: list[str] = typer.Option(  # noqa: B008
        [],
        "--required-field",
        help="A field every card of this type should state; repeatable. Marks a --field of "
        "the same name required, or adds the field after them.",
    ),
    grain: str | None = typer.Option(
        None,
        "--grain",
        help="'card': each instance gets its own card. 'row': each is a Facts row on the "
        "card it belongs to.",
    ),
    status: str | None = typer.Option(
        None,
        "--status",
        help="'deprecated' turns the type off for the project (or the organization); "
        "'active' turns it on.",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Change a card type for a project, or for the organization with --org-wide.

    Only what is given changes; a repeated flag replaces that list. Changing a
    built-in or organization type from a project gives the project its own copy
    and changes nothing elsewhere; `beakr kb types reset` drops the copy. Cards
    already written are not rewritten; later compiles use the new definition.
    """
    key, where = _where(key, project, org_wide)
    if grain is not None and grain not in card_types.GRAINS:
        _usage("--grain is 'card' or 'row'.")
    if status is not None and status not in card_types.STATUSES:
        _usage("--status is 'active' or 'deprecated'.")
    changes = card_types.changes(
        {
            "label": label,
            "description": description,
            "when_to_use": when_to_use,
            "when_not_to_use": when_not_to_use,
            "aliases": list(alias) if alias else None,
            "fields": _field_list(field, required_field) if field or required_field else None,
            "grain": grain,
            "status": status,
        }
    )
    if not changes:
        _usage(
            "Nothing to change: pass at least one of --label, --description, --when-to-use, "
            "--when-not-to-use, --alias, --field, --required-field, --grain or --status."
        )
    with get_sdk() as sdk:
        saved = dict(sdk.knowledge.update_card_type(key, **where, **changes))
    page_type = saved.get("page_type") or {}
    err_console.print(
        f"Changed the card type {page_type.get('label') or key} ({key}) for "
        f"{card_types.where(where['scope'])}. Cards already written are not rewritten; later "
        "compiles use this definition.",
        markup=False,
        highlight=False,
    )
    emit(
        saved,
        json_out=json_out,
        render=_render_type,
        lines=lambda data: [_line(data.get("page_type") or {})],
        hint=_reset_hint(key, where) if page_type.get("overrides") else None,
    )


def _reset_hint(key: str, where: dict[str, Any]) -> str:
    """The command that drops the copy this change made or changed."""
    target = f"-P {where['project_id']}" if where["scope"] == "project" else "--org-wide"
    quoted = f"'{key}'" if " " in key else key
    return f"beakr kb types reset {quoted} {target}"


@app.command("reset")
def reset_command(
    key: str = typer.Argument(..., help="The type's key, from `beakr kb types ls`."),
    project: str | None = _project_opt,
    org_wide: bool = _org_wide_opt,
    json_out: bool = _json_opt,
) -> None:
    """Remove a scope's own copy of a card type; it uses the type it inherits again.

    A type defined only in that scope has nothing to go back to (exit 5): turn
    it off with `beakr kb types edit KEY --status deprecated` instead.
    """
    key, where = _where(key, project, org_wide)
    with get_sdk() as sdk:
        removed = dict(sdk.knowledge.remove_card_type_copy(key, **where))
    emit(
        removed,
        json_out=json_out,
        render=lambda _removed: err_console.print(
            card_types.card_type_reset_text(key, where["scope"]), markup=False, highlight=False
        ),
        json_when_piped=False,
    )


#: Why a key was left out of a copy, in the words of the command that ran.
_SKIPPED = {
    "copy": {
        "not_found": "the source project does not define it",
        "exists": "the destination already defines it; pass --replace to overwrite it",
    },
    "promote": {
        "not_found": "the project does not define it",
        "exists": "the organization already defines it; pass --replace to overwrite it",
    },
}


def _keys(types: Any) -> list[str]:
    return [str(page_type.get("key")) for page_type in types or [] if isinstance(page_type, dict)]


def _copied_lines(result: dict[str, Any]) -> list[str]:
    """``copied<TAB>key``, ``replaced<TAB>key`` and ``skipped<TAB>key<TAB>reason``."""
    lines = [f"copied\t{key}" for key in _keys(result.get("copied"))]
    lines += [f"replaced\t{key}" for key in _keys(result.get("replaced"))]
    lines += [
        f"skipped\t{skipped.get('key')}\t{skipped.get('reason')}"
        for skipped in result.get("skipped") or []
    ]
    return lines or ["(nothing copied)"]


def _render_copied(command: str) -> Any:
    reasons = _SKIPPED[command]

    def render(result: dict[str, Any]) -> None:
        for label, keys in (
            ("Copied", _keys(result.get("copied"))),
            ("Replaced", _keys(result.get("replaced"))),
        ):
            if keys:
                console.print(f"{label}: {', '.join(keys)}", highlight=False, markup=False)
        for skipped in result.get("skipped") or []:
            reason = str(skipped.get("reason"))
            console.print(
                f"Skipped {skipped.get('key')}: {reasons.get(reason, reason)}",
                highlight=False,
                markup=False,
            )
        if not (result.get("copied") or result.get("replaced") or result.get("skipped")):
            console.print("Nothing was copied.", highlight=False, markup=False)

    return render


_keys_arg = typer.Argument(..., help="The keys of the types, from `beakr kb types ls`.")
_replace_opt = typer.Option(
    False, "--replace", help="Overwrite a type the destination already defines; else it is skipped."
)


@app.command("copy")
def copy_command(
    keys: list[str] = _keys_arg,
    source: str = typer.Option(
        ...,
        "--from",
        help="The project whose types to copy: id, slug or name.",
        callback=project_ref_callback,
    ),
    destination: str = typer.Option(
        ...,
        "--to",
        help="The project that gets its own copies: id, slug or name.",
        callback=project_ref_callback,
    ),
    replace: bool = _replace_opt,
    json_out: bool = _json_opt,
) -> None:
    """Copy card types one project defines into another project.

    The destination gets its own copies; you must be able to read the source
    project and own the destination. A key the source does not itself define is
    skipped, and so is one the destination already defines unless --replace.
    Cards are not copied or changed.
    """
    with get_sdk() as sdk:
        result = dict(
            sdk.knowledge.copy_card_types(source, destination, list(keys), replace=replace)
        )
    emit(result, json_out=json_out, render=_render_copied("copy"), lines=_copied_lines)


@app.command("promote")
def promote_command(
    keys: list[str] = _keys_arg,
    project: str | None = _project_opt,
    replace: bool = _replace_opt,
    json_out: bool = _json_opt,
) -> None:
    """Copy card types a project defines up to the organization.

    Every project without a copy of its own then inherits them. Needs an
    organization admin. A key the project does not itself define is skipped, and
    so is one the organization already defines unless --replace. Cards are not
    copied or changed.
    """
    project_id = _require_project(project)
    with get_sdk() as sdk:
        result = dict(sdk.knowledge.promote_card_types(project_id, list(keys), replace=replace))
    emit(result, json_out=json_out, render=_render_copied("promote"), lines=_copied_lines)


_apply_opt = typer.Option(
    False,
    "--apply",
    help="Make the change. Without it the command previews: it prints the counts and "
    "changes nothing.",
)


def _type_key(key: str, what: str = "key") -> str:
    key = key.strip()
    if not card_types.valid_type_key(key):
        _usage(
            f"{card_types.BAD_TYPE_KEY[0]} Pass the {what} exactly as `beakr kb types ls` "
            "prints it."
        )
    return key


def _rename_pairs(pairs: list[str]) -> list[dict[str, str]]:
    """``Old name=New name`` from each --rename, split at the first ``=``."""
    renames: list[dict[str, str]] = []
    for pair in pairs:
        old, sep, new = pair.partition("=")
        if not sep or not old.strip() or not new.strip():
            raise typer.BadParameter(
                f"expected 'Old name=New name', got {pair!r}", param_hint="--rename"
            )
        renames.append({"old": old.strip(), "new": new.strip()})
    return renames


def _emit_text(result: dict[str, Any], text: str, *, json_out: bool) -> None:
    """The answer as its sentences (a terminal and a pipe alike), or the JSON with --json."""
    emit(
        result,
        json_out=json_out,
        render=lambda _result: console.print(text, highlight=False, markup=False),
        lines=lambda _result: text.splitlines(),
    )


@app.command("rename-fields")
def rename_fields_command(
    key: str = typer.Argument(..., help="The type's key, from `beakr kb types ls`."),
    project: str | None = _project_opt,
    rename: list[str] = typer.Option(  # noqa: B008
        [],
        "--rename",
        "-r",
        help="One field to rename, as 'Old name=New name'; repeatable.",
    ),
    apply: bool = _apply_opt,
    json_out: bool = _json_opt,
) -> None:
    """Rename fields on every card of a type the project already has. Previews unless --apply.

    The type's own field list is renamed with the cards, and rows that become
    the same field are merged with their citations kept. A rename the safety
    checks do not accept is left out and named. One run changes at most 500
    cards and says how many are left; run it again for the rest. An applied
    change shows in the project's Activity, where a project owner can undo it.
    Only the project's owners may; exit 5 while Knowledge runs maintenance on
    the project.
    """
    key = _type_key(key)
    renames = _rename_pairs(rename)
    if not renames:
        _usage("Name the fields to rename: --rename 'Old name=New name' (repeatable).")
    project_id = _require_project(project)
    with get_sdk() as sdk:
        result = dict(
            sdk.knowledge.rename_card_type_fields(key, project_id, renames, dry_run=not apply)
        )
    _emit_text(
        result,
        card_updates.fields_renamed_text(result, card_updates.COMMAND_WORDS),
        json_out=json_out,
    )


@app.command("retype")
def retype_command(
    key: str = typer.Argument(..., help="The key of the type whose cards to move."),
    project: str | None = _project_opt,
    to_type: str = typer.Option(
        ...,
        "--to",
        help="The key of the type the cards become: an active type in the project whose "
        "instances are cards.",
    ),
    suggestion: str | None = typer.Option(
        None,
        "--suggestion",
        help="The suggestion this move answers, from `beakr kb types suggestions`; an "
        "--apply that leaves no card of the type marks it applied.",
    ),
    apply: bool = _apply_opt,
    json_out: bool = _json_opt,
) -> None:
    """Move every card of a type in the project to another type. Previews unless --apply.

    A card's text is not changed, so its rows keep their field names: if the
    two types name a field differently, run `beakr kb types rename-fields` on
    the target type afterwards. One run moves at most 500 cards and says how
    many are left. An applied change shows in the project's Activity, where a
    project owner can undo it. Only the project's owners may.
    """
    key = _type_key(key)
    target = _type_key(to_type, "--to key")
    project_id = _require_project(project)
    with get_sdk() as sdk:
        result = dict(
            sdk.knowledge.retype_cards(
                key, project_id, target, dry_run=not apply, suggestion_id=suggestion
            )
        )
    _emit_text(
        result,
        card_updates.cards_retyped_text(result, card_updates.COMMAND_WORDS),
        json_out=json_out,
    )


def register(root: typer.Typer) -> None:
    """Attach ``kb types`` (the root is unused)."""
    kb_app.add_typer(app, name="types")
