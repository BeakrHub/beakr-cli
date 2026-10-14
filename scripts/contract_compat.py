# Vendored from beakr-engine beakr/api/public/compat.py; do not edit here.
# tests/test_release_prep.py fails when this copy drifts from a sibling engine
# checkout. scripts/prepare_release.py uses it to choose the SDK version bump.
"""Breaking-change detection between two versions of the public OpenAPI document.

The public API promises that a client written against version N keeps working
on every N.x (``docs/technical/api-versioning.md``). This module states that
promise as rules ``tests/contract/test_public_api_compat.py`` runs against an
immutable base contract (the merge base, never the working-tree snapshot).

The rules are about what a client already depends on, so requests and responses
are judged in opposite directions:

- **Requests** may only accept more. Breaking: a parameter, body or property
  becoming required; a type changing; an enum losing a value; ``null`` no
  longer accepted; an array item or a nested object's property narrowing in the
  same ways; a request content type disappearing.
- **Responses** may only promise more. Breaking: a property, at any depth,
  disappearing; a type changing; a value becoming nullable that was not; an
  array item changing type; a success status code or response content type
  disappearing. New response enum values are additive by policy (clients must
  tolerate unknown values), so they are not reported.
- **Operations**: removal, a different required scope, or an anonymous
  operation starting to require authentication. An operation that leaves the
  documented contract but is still served to the same credentials is
  *unlisted*, not removed (``beakr/api/public/core.py``): a client written
  against it keeps working, so it is reported by ``unlisted_operations`` for
  the changelog rather than as a break.

Schemas are resolved through ``$ref`` and merged through ``allOf``; ``anyOf`` /
``oneOf`` are compared as the set of their resolved member types.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_METHODS = ("get", "post", "put", "patch", "delete")
_MAX_DEPTH = 12


def _resolve(spec: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if depth > 20 or not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        target = spec.get("components", {}).get("schemas", {}).get(name, {})
        return _resolve(spec, target, depth + 1)
    return node


def _variants(spec: dict[str, Any], schema: Any) -> list[dict[str, Any]]:
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict):
        return []
    members = schema.get("anyOf") or schema.get("oneOf")
    if members:
        found: list[dict[str, Any]] = []
        for member in members:
            found.extend(_variants(spec, member))
        return found
    return [schema]


def _object(spec: dict[str, Any], schema: Any) -> tuple[dict[str, Any], set[str]]:
    """Properties and required names of an object schema, through refs and allOf."""
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict):
        return {}, set()
    properties: dict[str, Any] = dict(schema.get("properties", {}))
    required: set[str] = set(schema.get("required", []))
    for part in schema.get("allOf", []):
        more, more_required = _object(spec, part)
        properties.update(more)
        required |= more_required
    return properties, required


def _types(spec: dict[str, Any], schema: Any) -> set[str]:
    """The JSON types a schema admits ('null' included); empty means unconstrained."""
    types: set[str] = set()
    for variant in _variants(spec, schema):
        declared = variant.get("type")
        if isinstance(declared, list):
            types.update(str(t) for t in declared)
        elif declared:
            types.add(str(declared))
        elif "properties" in variant or "allOf" in variant:
            types.add("object")
        else:
            return set()  # an unconstrained member admits anything
        if variant.get("nullable"):
            types.add("null")
    return types


def _enum(spec: dict[str, Any], schema: Any) -> set[Any] | None:
    values: set[Any] = set()
    for variant in _variants(spec, schema):
        if "enum" not in variant:
            return None
        values.update(v for v in variant["enum"] if isinstance(v, (str, int, float, bool)))
    return values


def _item(spec: dict[str, Any], schema: Any) -> Any:
    for variant in _variants(spec, schema):
        if variant.get("type") == "array" or "items" in variant:
            return variant.get("items")
    return None


def _compare(
    where: str,
    old_spec: dict[str, Any],
    old: Any,
    new_spec: dict[str, Any],
    new: Any,
    *,
    request: bool,
    depth: int = 0,
) -> list[str]:
    if old is None or new is None or depth > _MAX_DEPTH:
        return []
    problems: list[str] = []
    old_types, new_types = _types(old_spec, old), _types(new_spec, new)
    if old_types and new_types:
        if request:
            # A request schema must still accept every type it accepted.
            lost = old_types - new_types
            if lost:
                problems.append(f"{where}: no longer accepts {', '.join(sorted(lost))}")
        else:
            # A response must not start producing a type clients never saw.
            gained = new_types - old_types
            if gained:
                problems.append(f"{where}: may now be {', '.join(sorted(gained))}")
    elif request and old_types == set() and new_types:
        problems.append(f"{where}: now restricted to {', '.join(sorted(new_types))}")
    elif not request and new_types == set() and old_types:
        problems.append(f"{where}: no longer has a declared type")

    if request:
        old_enum, new_enum = _enum(old_spec, old), _enum(new_spec, new)
        if new_enum is not None and (old_enum is None or old_enum - new_enum):
            dropped = sorted(map(str, (old_enum or set()) - new_enum)) or ["any value"]
            problems.append(f"{where}: no longer accepts {', '.join(dropped)}")

    old_properties, old_required = _object(old_spec, old)
    new_properties, new_required = _object(new_spec, new)
    if request:
        for name in sorted(new_required - old_required):
            problems.append(f"{where}.{name}: is now required")
        for name, old_property in sorted(old_properties.items()):
            if name in new_properties:
                problems.extend(
                    _compare(
                        f"{where}.{name}",
                        old_spec,
                        old_property,
                        new_spec,
                        new_properties[name],
                        request=True,
                        depth=depth + 1,
                    )
                )
    else:
        for name, old_property in sorted(old_properties.items()):
            if name not in new_properties:
                problems.append(f"{where}.{name}: was removed")
                continue
            problems.extend(
                _compare(
                    f"{where}.{name}",
                    old_spec,
                    old_property,
                    new_spec,
                    new_properties[name],
                    request=False,
                    depth=depth + 1,
                )
            )
        for name in sorted(old_required - new_required):
            if name in new_properties:
                problems.append(f"{where}.{name}: is no longer always present")

    problems.extend(
        _compare(
            f"{where}[]",
            old_spec,
            _item(old_spec, old),
            new_spec,
            _item(new_spec, new),
            request=request,
            depth=depth + 1,
        )
    )
    return problems


def _parameters(operation: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(p.get("in", ""), p.get("name", "")): p for p in operation.get("parameters", [])}


def _success_responses(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        code: response
        for code, response in operation.get("responses", {}).items()
        if str(code).startswith("2")
    }


def _operation_changes(
    where: str, old_spec: dict, old: dict, new_spec: dict, new: dict
) -> list[str]:
    problems: list[str] = []
    old_scope, new_scope = old.get("x-beakr-scope"), new.get("x-beakr-scope")
    if new_scope != old_scope and new_scope is not None:
        problems.append(f"{where}: required scope changed from {old_scope!r} to {new_scope!r}")
    if old.get("security") == [] and new.get("security") != []:
        problems.append(f"{where}: now requires authentication")

    old_params, new_params = _parameters(old), _parameters(new)
    for key, param in sorted(new_params.items()):
        location, name = key
        if param.get("required") and not old_params.get(key, {}).get("required"):
            problems.append(f"{where}: {location} parameter {name!r} is now required")
    for key, param in sorted(old_params.items()):
        location, name = key
        if key not in new_params:
            if location == "path":
                problems.append(f"{where}: path parameter {name!r} was removed")
            continue
        problems.extend(
            _compare(
                f"{where} {location} parameter {name!r}",
                old_spec,
                param.get("schema"),
                new_spec,
                new_params[key].get("schema"),
                request=True,
            )
        )

    old_body, new_body = old.get("requestBody", {}), new.get("requestBody", {})
    if new_body.get("required") and not old_body.get("required"):
        problems.append(f"{where}: request body is now required")
    old_body_content = old_body.get("content", {})
    new_body_content = new_body.get("content", {})
    for media, old_media in sorted(old_body_content.items()):
        if new_body_content and media not in new_body_content:
            problems.append(f"{where}: no longer accepts a {media} body")
            continue
        if media in new_body_content:
            problems.extend(
                _compare(
                    f"{where} request",
                    old_spec,
                    old_media.get("schema"),
                    new_spec,
                    new_body_content[media].get("schema"),
                    request=True,
                )
            )

    old_ok, new_ok = _success_responses(old), _success_responses(new)
    for code, old_response in sorted(old_ok.items()):
        if code not in new_ok:
            problems.append(f"{where}: no longer answers {code}")
            continue
        new_content = new_ok[code].get("content", {})
        for media, old_media in sorted(old_response.get("content", {}).items()):
            if media not in new_content:
                problems.append(f"{where}: {code} no longer returns {media}")
                continue
            problems.extend(
                _compare(
                    f"{where} {code} response",
                    old_spec,
                    old_media.get("schema"),
                    new_spec,
                    new_content[media].get("schema"),
                    request=False,
                )
            )
    return problems


# (method, path, scope) -> whether the app still serves that operation to a
# credential holding ``scope``.
StillServed = Callable[[str, str, str | None], bool]


def _missing_operations(
    old_spec: dict[str, Any], new_spec: dict[str, Any]
) -> list[tuple[str, str, dict[str, Any]]]:
    new_paths = new_spec.get("paths", {})
    return [
        (method.upper(), path, item[method])
        for path, item in sorted(old_spec.get("paths", {}).items())
        for method in _METHODS
        if method in item and method not in new_paths.get(path, {})
    ]


def _served(still_served: StillServed | None, method: str, path: str, op: dict) -> bool:
    return still_served is not None and still_served(method, path, op.get("x-beakr-scope"))


def unlisted_operations(
    old_spec: dict[str, Any], new_spec: dict[str, Any], still_served: StillServed
) -> list[str]:
    """Operations ``new_spec`` no longer documents that are still served unchanged."""
    return [
        f"{method} {path}"
        for method, path, op in _missing_operations(old_spec, new_spec)
        if _served(still_served, method, path, op)
    ]


def breaking_changes(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
    still_served: StillServed | None = None,
) -> list[str]:
    """Every rule above that ``new_spec`` breaks for a client of ``old_spec``.

    With ``still_served``, an operation missing from ``new_spec`` that the app
    still serves under the same scope is unlisted rather than removed.
    """
    problems: list[str] = [
        f"{method} {path}: operation removed"
        for method, path, op in _missing_operations(old_spec, new_spec)
        if not _served(still_served, method, path, op)
    ]
    new_paths = new_spec.get("paths", {})
    for path, item in sorted(old_spec.get("paths", {}).items()):
        for method in _METHODS:
            old = item.get(method)
            new = new_paths.get(path, {}).get(method)
            if old is None or new is None:
                continue
            where = f"{method.upper()} {path}"
            problems.extend(_operation_changes(where, old_spec, old, new_spec, new))
    return problems


def major_version(spec: dict[str, Any]) -> int:
    version = str(spec.get("info", {}).get("version", "0"))
    try:
        return int(version.split(".", 1)[0])
    except ValueError:
        return 0
