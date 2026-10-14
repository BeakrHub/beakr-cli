"""Render the CLI's private client for the unlisted operations it calls.

    uv run python scripts/generate_private_api.py [ENGINE_PATH]
    uv run python scripts/generate_private_api.py --check [ENGINE_PATH]   # exit 1 when stale

The public SDK (beakr-sdk) covers only the documented contract. The few
unlisted operations the CLI and its stdio MCP server call are listed, one per
line, in ``src/beakr_cli/_private_api/operations.allowlist``. This script takes
the engine's ``docs/api/internal-openapi.json``, keeps exactly those operations
(and the schemas they reference), and renders them with the SDK's own generator
(``beakr_sdk._codegen``) into ``src/beakr_cli/_private_api/_generated``: a
request builder per operation and the ``private_api`` clients.

It refuses an allowlist line the internal contract does not have, and one the
public contract documents (call that operation through the SDK instead).

ENGINE_PATH defaults to $BEAKR_ENGINE_PATH, then ../beakr-engine, then the committed
snapshot ``snapshots/beakr-engine``, whose
``docs/api/cli-private-openapi.json`` is the internal contract already filtered to
the allowlist.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from beakr_sdk._codegen import METHODS, Target, render

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src/beakr_cli/_private_api"
ALLOWLIST = PACKAGE / "operations.allowlist"
OUT = PACKAGE / "_generated"
INTERNAL = Path("docs/api/internal-openapi.json")
PUBLIC = Path("docs/api/public-openapi.json")
#: The engine snapshot's stand-in for INTERNAL (scripts/refresh_snapshots.py):
#: the internal contract already filtered to the allowlist, as this script filters it.
CLI_PRIVATE = Path("docs/api/cli-private-openapi.json")

INIT = '''"""Generated request builders for the unlisted operations the CLI calls. Do not edit;
regenerate with ``uv run python scripts/generate_private_api.py``.

- :mod:`beakr_cli._private_api._generated.models`: a ``TypedDict`` per schema those
  operations use.
- :mod:`beakr_cli._private_api._generated.operations`: a request builder per
  operation on ``operations.allowlist`` and the ``private_api`` clients.
"""
'''

TARGET = Target(
    package="beakr_cli._private_api._generated",
    document="internal OpenAPI document, filtered to the CLI's operations.allowlist",
    init=INIT,
    audience="private",
    generator="scripts/generate_private_api.py",
    # The route's tag names another area; kept as the SDK named it before.
    path_areas=(("/v1/usage", "usage"),),
)

_LINE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/\S+)$")


def engine_path(argument: str | None) -> Path:
    candidates = [argument, os.environ.get("BEAKR_ENGINE_PATH")]
    candidates += [
        str(ROOT.parent / "beakr-engine"),
        # The committed snapshot (scripts/refresh_snapshots.py), what CI reads.
        str(ROOT / "snapshots" / "beakr-engine"),
    ]
    for candidate in candidates:
        if candidate and private_source(Path(candidate)).exists():
            return Path(candidate)
    raise SystemExit("No engine checkout with docs/api/internal-openapi.json found.")


def private_source(engine: Path) -> Path:
    """The engine's internal contract, else (in a snapshot) its allowlisted part."""
    return engine / INTERNAL if (engine / INTERNAL).exists() else engine / CLI_PRIVATE


def read_allowlist(text: str) -> list[tuple[str, str]]:
    """``(METHOD, path)`` per line; comments and blank lines skipped, duplicates refused."""
    entries: list[tuple[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if match is None:
            raise ValueError(f"operations.allowlist:{number}: expected 'METHOD /path', got {raw!r}")
        entry = (match.group(1), match.group(2))
        if entry in entries:
            raise ValueError(f"operations.allowlist:{number}: {line} is listed twice")
        entries.append(entry)
    return entries


def operations_of(spec: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method in METHODS
    }


def filter_contract(
    internal: dict[str, Any], public: dict[str, Any], allowed: list[tuple[str, str]]
) -> dict[str, Any]:
    """The internal contract reduced to the allowlisted operations and the schemas they use."""
    reachable = operations_of(internal)
    documented = operations_of(public)
    # Documented first: a snapshot's filtered contract no longer holds them.
    public_now = [f"{m} {p}" for m, p in allowed if (m, p) in documented]
    if public_now:
        raise ValueError(
            f"documented now, call them through beakr-sdk instead: {', '.join(public_now)}"
        )
    missing = [f"{m} {p}" for m, p in allowed if (m, p) not in reachable]
    if missing:
        raise ValueError(f"not in the internal contract: {', '.join(missing)}")
    keep = set(allowed)
    paths: dict[str, Any] = {}
    for path, item in internal["paths"].items():
        kept = {
            key: value
            for key, value in item.items()
            if key not in METHODS or (key.upper(), path) in keep
        }
        if any(key in METHODS for key in kept):
            paths[path] = kept
    schemas = internal.get("components", {}).get("schemas", {})
    used = _referenced_schemas(paths, schemas)
    filtered: dict[str, Any] = {
        key: value for key, value in internal.items() if key not in ("paths", "components")
    }
    filtered["paths"] = paths
    filtered["components"] = {"schemas": {name: schemas[name] for name in sorted(used)}}
    return filtered


def _referenced_schemas(root: Any, schemas: dict[str, Any]) -> set[str]:
    """Every component schema ``root`` references, transitively."""
    found: set[str] = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in found:
                    found.add(name)
                    pending.append(schemas[name])
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return found


def render_private_api(engine: Path, allowlist_text: str) -> dict[str, str]:
    """Every generated file's content, keyed by its name under ``_generated``."""
    internal = json.loads(private_source(engine).read_text())
    public = json.loads((engine / PUBLIC).read_text())
    filtered = filter_contract(internal, public, read_allowlist(allowlist_text))
    # The header's digest is of the filtered document, so it changes only when
    # an allowlisted operation (or a schema it uses) changes.
    return render(json.dumps(filtered, indent=1, sort_keys=True), TARGET)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    positional = [arg for arg in argv if arg != "--check"]
    engine = engine_path(positional[0] if positional else None)
    try:
        files = render_private_api(engine, ALLOWLIST.read_text())
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    if check:
        stale = [
            str((OUT / name).relative_to(ROOT))
            for name, text in files.items()
            if not (OUT / name).exists() or (OUT / name).read_text() != text
        ]
        if stale:
            print(f"stale: {', '.join(stale)}; run scripts/generate_private_api.py")
            return 1
        print("CLI private client is current")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text)
    count = files["operations.py"].count("\ndef build_")
    print(f"wrote {OUT} ({count} operations) from {private_source(engine)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
