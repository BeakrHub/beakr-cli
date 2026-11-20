"""Card types: what ``edit_card_type`` and ``beakr kb types`` share.

The stdio ``edit_card_type`` tool answers the same text as the hosted one for
the same REST answers (beakr-engine ``beakr/api/mcp_remote/card_type_tools.py``),
so the argument handling, the refusals and the two result texts here are that
module's, character for character; the engine's
``tests/unit/test_mcp_every_tool_result_contract.py`` runs both servers on one
canned answer and compares. ``beakr kb types`` checks a type key by the same
rule before it sends anything.
"""

from __future__ import annotations

import re
from typing import Any

#: What a caller can change, as the route names it.
TEXT_FIELDS = ("label", "description", "when_to_use", "when_not_to_use")
SCOPES = ("project", "org")
GRAINS = ("card", "row")
STATUSES = ("active", "deprecated")
#: The key becomes a path segment: anything else (a slash, a dot) could name a
#: different route.
TYPE_KEY = re.compile(r"[A-Za-z0-9_ -]{1,80}")

NO_TYPE = (
    "Name the card type.",
    "Pass `type`: a type key from knowledge_base command='ontology'.",
)
BAD_TYPE_KEY = (
    "A card type's key is letters, digits, spaces, hyphens and underscores.",
    "Pass `type` exactly as knowledge_base command='ontology' prints it.",
)
BAD_SCOPE = ("scope is 'project' or 'org'.", None)
ORG_WITH_PROJECT = (
    "An organization-wide change names no project.",
    "Leave `project` out with scope 'org', or use scope 'project'.",
)
NO_PROJECT = (
    "Name the project.",
    "Pass `project`: a project name or id from list_projects.",
)
NOTHING_TO_CHANGE = (
    "Nothing to change.",
    "Pass at least one of label, description, when_to_use, when_not_to_use, "
    "aliases, fields, grain or status; or reset=true to drop this copy.",
)


def valid_type_key(key: str) -> bool:
    """Whether ``key`` can be sent as the route's one path segment."""
    return bool(TYPE_KEY.fullmatch(key))


def fields(value: Any) -> list[dict[str, Any]] | None:
    """``fields`` as the route takes it: a name alone is a field that is not required."""
    if not isinstance(value, list):
        return None
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append({"name": item.strip(), "required": False})
        elif isinstance(item, dict) and str(item.get("name") or "").strip():
            out.append({"name": str(item["name"]).strip(), "required": bool(item.get("required"))})
    return out


def changes(args: dict[str, Any]) -> dict[str, Any]:
    """The changes a call names, as the route's body fields."""
    out: dict[str, Any] = {
        key: args[key] for key in TEXT_FIELDS if isinstance(args.get(key), str) and args[key]
    }
    if isinstance(args.get("aliases"), list):
        out["aliases"] = [str(a) for a in args["aliases"] if str(a).strip()]
    listed = fields(args.get("fields"))
    if listed is not None:
        out["fields"] = listed
    for key in ("grain", "status"):
        if args.get(key):
            out[key] = args[key]
    return out


def where(scope: str) -> str:
    return "this project" if scope == "project" else "the organization"


def card_type_saved_text(page_type: dict[str, Any], scope: str) -> str:
    """What the change left, in the words both servers answer with."""
    label = page_type.get("label") or page_type.get("key")
    lines = [f"Changed the card type {label} ({page_type.get('key')}) for {where(scope)}."]
    schema_ = page_type.get("metadata_schema")
    schema_ = schema_ if isinstance(schema_, dict) else {}
    required = {str(name).casefold() for name in schema_.get("required_attributes") or []}
    names = [str(name) for name in schema_.get("key_attributes") or []]
    if names:
        lines.append(
            "Fields: "
            + "; ".join(
                f"{name} (required)" if name.casefold() in required else name for name in names
            )
        )
    if schema_.get("grain") == "row":
        lines.append("Each instance is a Facts row on the card it belongs to, not its own card.")
    elif schema_.get("grain") == "card":
        lines.append("Each instance gets its own card.")
    if page_type.get("status") == "deprecated":
        lines.append(f"It is turned off: compiles for {where(scope)} no longer use this type.")
    overrides = page_type.get("overrides")
    if overrides:
        broader = "built-in" if overrides == "global" else "organization's"
        lines.append(
            f"This is {where(scope)}'s own copy; the {broader} type is unchanged. "
            "reset=true drops the copy."
        )
    lines.append("Cards already written are not rewritten; later compiles use this definition.")
    return "\n".join(lines)


def card_type_reset_text(key: str, scope: str) -> str:
    return (
        f"Removed the copy of the card type {key} for {where(scope)}; "
        "it uses the type it inherits again."
    )
