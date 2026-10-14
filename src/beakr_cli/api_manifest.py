"""The ``beakr api`` command table, derived from the public API contract.

Every public operation gets one command, ``beakr api <area> <action>``, so the
CLI reaches everything the web app and the SDKs reach (developer platform W7:
"the CLI covers every web action"). The table is built from the engine's
``docs/api/public-openapi.json`` by :func:`build_manifest` and vendored as
``data/operations.json`` (``scripts/sync_operations.py`` refreshes it, and
``tests/test_cli_api_commands.py`` fails when it is stale), so the commands
cannot drift from the contract and the CLI starts without parsing it.

Naming follows the API reference: the area is the operation's first non-public
tag (the same page the reference puts it on), and the action is the handler's
name in kebab case (``create_project_route`` -> ``create-project``). Deprecated
aliases get no command of their own; their canonical operation has one.
"""

from __future__ import annotations

import re
from typing import Any

_METHODS = ("get", "post", "put", "patch", "delete")
_SCALARS = {"string", "integer", "number", "boolean"}
#: Body fields that would shadow the command's own options.
_RESERVED = {"json", "data", "all", "out", "help"}


def _resolve(spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    """A schema with ``$ref`` followed and ``X | null`` unions collapsed to ``X``."""
    seen = 0
    while isinstance(schema, dict) and seen < 20:
        seen += 1
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            schema = spec.get("components", {}).get("schemas", {}).get(ref.rsplit("/", 1)[-1], {})
            continue
        members = schema.get("anyOf") or schema.get("oneOf")
        if members:
            real = [m for m in members if not (isinstance(m, dict) and m.get("type") == "null")]
            if len(real) == 1:
                schema = {
                    **{k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")},
                    **real[0],
                }
                continue
        break
    return schema if isinstance(schema, dict) else {}


def _type_of(schema: dict[str, Any]) -> str | None:
    """A schema's one JSON type; ``None`` when it allows several.

    JSON Schema 2020-12 (OpenAPI 3.1) may spell ``type`` as a list, e.g.
    ``["string", "null"]`` or ``["object", "string"]``. ``null`` is dropped;
    a value that may still be more than one type is passed as JSON.
    """
    declared = schema.get("type")
    if isinstance(declared, list):
        real = [t for t in declared if t != "null"]
        return str(real[0]) if len(real) == 1 else None
    return declared if isinstance(declared, str) else None


def _kind(spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    """The CLI shape of one parameter or field: type, enum, list-ness, binary."""
    resolved = _resolve(spec, schema)
    declared = _type_of(resolved)
    if declared == "array":
        item = _resolve(spec, resolved.get("items", {}))
        item_type = _type_of(item)
        if item_type in _SCALARS:
            out: dict[str, Any] = {"type": item_type, "multiple": True}
            if item.get("enum"):
                out["enum"] = [str(v) for v in item["enum"]]
            return out
        return {"type": "object"}
    if declared in _SCALARS:
        out = {"type": declared}
        # OpenAPI 3.0 spells an uploaded file ``format: binary``; 3.1 (what
        # FastAPI emits) spells it ``contentMediaType``.
        if resolved.get("format") == "binary" or resolved.get("contentMediaType"):
            out["binary"] = True
        if resolved.get("enum"):
            out["enum"] = [str(v) for v in resolved["enum"]]
        return out
    return {"type": "object"}


def _area(operation: dict[str, Any]) -> str:
    tags = [t for t in operation.get("tags", []) if t != "public"]
    name = sorted(tags)[0] if tags else "general"
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "general"


def _action(operation: dict[str, Any], method: str, path: str) -> str:
    operation_id = str(operation.get("operationId") or "")
    suffix = re.sub(r"\W", "_", path) + "_" + method
    name = operation_id[: -len(suffix)] if operation_id.endswith(suffix) else operation_id
    name = re.sub(r"_route$", "", name.strip("_")) or f"{method}"
    return name.replace("_", "-")


def _field(spec: dict[str, Any], name: str, schema: Any, required: bool) -> dict[str, Any]:
    resolved = _resolve(spec, schema)
    entry = {"name": name, "required": required, **_kind(spec, schema)}
    description = resolved.get("description") or (schema or {}).get("description")
    if description:
        entry["description"] = " ".join(str(description).split())
    if "default" in resolved and entry["type"] in _SCALARS and not entry.get("multiple"):
        entry["default"] = resolved["default"]
    return entry


def _body(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    request = operation.get("requestBody")
    if not request:
        return None
    content = request.get("content", {})
    media = "multipart" if "multipart/form-data" in content else "json"
    schema = content.get("multipart/form-data" if media == "multipart" else "application/json", {})
    resolved = _resolve(spec, schema.get("schema", {}))
    required = set(resolved.get("required", []))
    fields = []
    if resolved.get("type") == "object" or "properties" in resolved:
        for name, field_schema in sorted(resolved.get("properties", {}).items()):
            entry = _field(spec, name, field_schema, name in required)
            if name in _RESERVED:
                entry["option"] = f"body-{name}"
            fields.append(entry)
    return {
        "media": media,
        "required": bool(request.get("required")),
        "fields": fields,
        # A body that is not an object (a bare list) is only ever given whole.
        "object": resolved.get("type") == "object" or "properties" in resolved,
    }


def _response(spec: dict[str, Any], operation: dict[str, Any]) -> tuple[str, bool]:
    for code, response in sorted(operation.get("responses", {}).items()):
        if not str(code).startswith("2"):
            continue
        content = response.get("content", {})
        if not content:
            return "none", False
        if "application/json" in content:
            schema = _resolve(spec, content["application/json"].get("schema", {}))
            props = schema.get("properties", {})
            return "json", "next_cursor" in props and "data" in props
        if "text/event-stream" in content:
            return "sse", False
        media = next(iter(content))
        if media.startswith("text/"):
            return "text", False
        return "bytes", False
    return "none", False


def build_manifest(spec: dict[str, Any]) -> dict[str, Any]:
    """One command per public, non-deprecated operation, deterministically ordered."""
    entries: list[dict[str, Any]] = []
    for path, item in spec.get("paths", {}).items():
        for method in _METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict) or operation.get("deprecated"):
                continue
            path_params, query = [], []
            for param in operation.get("parameters", []):
                entry = _field(
                    spec, param["name"], param.get("schema", {}), bool(param.get("required"))
                )
                if param.get("description"):
                    entry["description"] = " ".join(str(param["description"]).split())
                if param.get("in") == "path":
                    path_params.append(entry)
                elif param.get("in") == "query":
                    query.append(entry)
            response, paginated = _response(spec, operation)
            entries.append(
                {
                    "op": f"{method.upper()} {path}",
                    "method": method.upper(),
                    "path": path,
                    "area": _area(operation),
                    "action": _action(operation, method, path),
                    "summary": " ".join(str(operation.get("summary") or "").split()),
                    "scope": operation.get("x-beakr-scope"),
                    "path_params": [
                        p
                        for name in re.findall(r"{([^}]+)}", path)
                        for p in path_params
                        if p["name"] == name
                    ],
                    "query": sorted(query, key=lambda q: q["name"]),
                    "body": _body(spec, operation),
                    "response": response,
                    "paginated": paginated,
                    # ``security: []`` in the contract: callable before sign-in,
                    # and sent without a credential even when one exists.
                    "anonymous": operation.get("security") == [],
                    # Deletes, purges and revocations: confirmed or ``--yes``.
                    "destructive": bool(operation.get("x-beakr-destructive")),
                }
            )
    entries.sort(key=lambda e: (e["area"], e["action"], e["op"]))
    _disambiguate(entries)
    return {
        "source": "docs/api/public-openapi.json",
        "version": spec.get("info", {}).get("version"),
        "operations": entries,
    }


def _disambiguate(entries: list[dict[str, Any]]) -> None:
    """Two operations with one handler name in an area keep distinct, stable names."""
    by_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        by_name.setdefault((entry["area"], entry["action"]), []).append(entry)
    for group in by_name.values():
        if len(group) < 2:
            continue
        for entry in group:
            tail = [s for s in entry["path"].split("/") if s and not s.startswith("{")][-1]
            entry["action"] = f"{entry['action']}-{tail}-{entry['method'].lower()}"
