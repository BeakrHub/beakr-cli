"""The CLI's private client holds exactly the unlisted operations the CLI calls.

The public SDK exposes only the documented contract, so the unlisted routes the
CLI and its stdio MCP server still call moved into the CLI
(``beakr_cli._private_api``), generated from the engine's internal contract
filtered to ``operations.allowlist``. The CLI is published too, so the list is
kept minimal and reviewed: these tests fail when

- a call site uses an unlisted operation that is not on the list (a builder or
  ``private_api`` method that does not exist, or a raw ``/v1/...`` route that is
  neither documented nor listed);
- a listed operation has no call site;
- the generated layer is not exactly the list, or is stale against the engine;
- a listed operation became documented (call it through the SDK instead);
- the keyed creates the client registers for replay are not the engine's.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from beakr_cli._private_api import REPLAYING_CREATES
from beakr_cli._private_api._generated.operations import OPERATIONS
from tests._engine import engine_checkout, engine_derived, refresh_snapshots

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src/beakr_cli"
_PACKAGE = _SRC / "_private_api"
_GENERATED = _PACKAGE / "_generated"


def _generator():
    path = _ROOT / "scripts/generate_private_api.py"
    spec = importlib.util.spec_from_file_location("_generate_private_api", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


_GEN = _generator()


def _allowlist() -> list[tuple[str, str]]:
    return _GEN.read_allowlist((_PACKAGE / "operations.allowlist").read_text())


def _source_files() -> list[Path]:
    return [path for path in sorted(_SRC.rglob("*.py")) if _GENERATED not in path.parents]


#: ``ops.build_<area>_<name>(`` in a resource; ``.private_api.<area>.<name>(`` anywhere.
_BUILDER = re.compile(r"\bops\.(build_[a-z0-9_]+)\b")
_PRIVATE_API = re.compile(r"\.private_api\.([a-z0-9_]+)\.([a-z0-9_]+)\(")


def call_site_operations(sources: dict[str, str]) -> dict[str, set[str]]:
    """``area.name`` of every private operation each source refers to, by file.

    A builder named ``build_<area>_<name>`` is matched against the generated
    operation keys, so a reference to an operation that was never generated
    surfaces as an unknown ``?build_...`` entry.
    """
    builders = {f"build_{key.replace('.', '_', 1)}": key for key in OPERATIONS}
    found: dict[str, set[str]] = {}
    for name, text in sources.items():
        keys = {builders.get(b, f"?{b}") for b in _BUILDER.findall(text)}
        keys |= {
            f"{area}.{method.removesuffix('_all')}" for area, method in _PRIVATE_API.findall(text)
        }
        if keys:
            found[name] = keys
    return found


def _private_sources() -> dict[str, str]:
    """Sources that may send private operations: the resources and every call site."""
    sources: dict[str, str] = {}
    for path in _source_files():
        text = path.read_text()
        # Only the private resources import the private builders as ``ops``.
        if "_private_api._generated import operations as ops" in text or ".private_api." in text:
            sources[str(path.relative_to(_ROOT))] = text
    return sources


def test_the_generated_layer_is_exactly_the_allowlist() -> None:
    assert sorted(OPERATIONS.values()) == sorted(_allowlist())


def test_every_call_site_operation_is_listed_and_every_listed_one_is_used() -> None:
    used = call_site_operations(_private_sources())
    referenced = set().union(*used.values())
    unknown = {
        name: sorted(k for k in keys if k not in OPERATIONS)
        for name, keys in used.items()
        if any(k not in OPERATIONS for k in keys)
    }
    assert unknown == {}, f"call sites use operations missing from operations.allowlist: {unknown}"
    unused = sorted(f"{OPERATIONS[k][0]} {OPERATIONS[k][1]}" for k in set(OPERATIONS) - referenced)
    assert unused == [], f"operations.allowlist lists operations nothing calls: {unused}"


def _route_literals(text: str) -> set[str]:
    """``/v1/...`` routes in a module's code strings (docstrings left out), f-strings included."""
    tree = ast.parse(text)
    skipped = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    # The literal parts of an f-string are read with the f-string, not alone.
    skipped |= {
        id(part)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
    }
    routes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text_value = "".join(
                v.value if isinstance(v, ast.Constant) else "{}" for v in node.values
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skipped
        ):
            text_value = node.value
        else:
            continue
        routes |= set(re.findall(r"/v1/[A-Za-z0-9_{}./-]*", text_value))
    return routes


def _template(route: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", route).rstrip("/.")


def _matches(route: str, templates: set[str]) -> bool:
    """A route names a known template, or is a leading part of one the code extends."""
    reduced = _template(route)
    return reduced in templates or (
        not reduced.endswith("{}") and any(t.startswith(reduced + "/") for t in templates)
    )


def test_no_call_site_sends_an_unlisted_route_by_hand() -> None:
    # The `beakr api` table is the documented contract (its own test keeps it so).
    documented = json.loads((_SRC / "data/operations.json").read_text())["operations"]
    known = {_template(entry["path"]) for entry in documented}
    known |= {_template(path) for _method, path in _allowlist()}
    stray = {
        str(path.relative_to(_ROOT)): sorted(r for r in routes if not _matches(r, known))
        for path in _source_files()
        if (routes := _route_literals(path.read_text()))
    }
    stray = {name: routes for name, routes in stray.items() if routes}
    assert stray == {}, f"routes neither documented nor on operations.allowlist: {stray}"


def test_the_scanners_catch_an_unlisted_operation() -> None:
    # Guards the gates themselves.
    used = call_site_operations(
        {
            "a.py": "ops.build_workflows_dispatch_workflow(body=b)",
            "b.py": "sdk.private_api.skills.list_library_skills(limit=5)",
        }
    )
    assert used == {
        "a.py": {"?build_workflows_dispatch_workflow"},
        "b.py": {"skills.list_library_skills"},
    }
    assert _route_literals('x = f"{base}/v1/workflows/{wid}/dispatch"\n') == {
        "/v1/workflows/{}/dispatch"
    }
    assert _route_literals('"""Calls /v1/secret."""\n') == set()
    assert not _matches("/v1/workflows/dispatch", {"/v1/workflows/runs"})


def test_the_generated_layer_is_current_with_the_engine() -> None:
    engine = engine_checkout("docs/api/public-openapi.json").parents[2]
    files = _GEN.render_private_api(engine, (_PACKAGE / "operations.allowlist").read_text())
    stale = [name for name, text in files.items() if (_GENERATED / name).read_text() != text]
    assert stale == [], f"stale: {stale}; run scripts/generate_private_api.py"


def test_a_documented_operation_is_refused() -> None:
    engine = engine_checkout("docs/api/public-openapi.json").parents[2]
    with pytest.raises(ValueError, match="documented now"):
        _GEN.render_private_api(engine, "GET /v1/projects\n")
    with pytest.raises(ValueError, match="not in the internal contract"):
        _GEN.render_private_api(engine, "GET /v1/no-such-route\n")


def test_the_registered_replaying_creates_are_the_engines() -> None:
    # The engine's own classification, else (in CI) the snapshot's copy of the
    # part of it the CLI can call.
    snapshots = refresh_snapshots()
    keyed = set(
        json.loads(
            engine_derived(
                snapshots.IDEMPOTENCY_TESTS,
                snapshots.REPLAYING_CREATES,
                lambda text: json.dumps(sorted(snapshots.keyed_creates(text))),
            )
        )
    )
    listed_posts = {path for method, path in _allowlist() if method == "POST"}
    assert sorted(REPLAYING_CREATES) == sorted(keyed & listed_posts)


def _resource_paths() -> dict[str, str]:
    """Class name -> the attribute path the client exposes it at (``knowledge.pages``)."""
    init = (_PACKAGE / "resources/__init__.py").read_text()
    paths = {
        cls: attr for attr, cls in re.findall(r"client\.([a-z_]+) = ([A-Za-z]+)\(client\)", init)
    }
    for path in (_PACKAGE / "resources").glob("*.py"):
        text = path.read_text()
        for owner in re.findall(r"^class ([A-Za-z]+)\(", text, re.M):
            body = text[text.index(f"class {owner}(") :]
            next_class = re.search(r"\nclass ", body[1:])
            body = body[: next_class.start() + 1] if next_class else body
            for attr, cls in re.findall(r"self\.([a-z_]+): ([A-Za-z]+)\[M\] = \2\(client\)", body):
                if owner in paths:
                    paths[cls] = f"{paths[owner]}.{attr}"
    return paths


def test_every_method_that_sends_a_listed_operation_is_called_by_the_cli() -> None:
    # A listed operation must be reached from a real call site, not only from a
    # resource method nothing calls; otherwise the list is not minimal.
    paths = _resource_paths()
    cli_code = "\n".join(
        path.read_text() for path in _source_files() if _PACKAGE not in path.parents
    )
    uncalled: list[str] = []
    for path in sorted((_PACKAGE / "resources").glob("*.py")):
        text = path.read_text()
        tree = ast.parse(text)
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            attr = paths.get(cls.name)
            for fn in cls.body:
                if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if fn.name.startswith("_") or not _BUILDER.search(
                    ast.get_source_segment(text, fn) or ""
                ):
                    continue
                called = attr is not None and re.search(
                    rf"\.{re.escape(attr)}\.{fn.name}\(", cli_code
                )
                # Another method of the package may call it (``iter`` -> ``list``).
                helper = re.search(rf"self\.{fn.name}\(", text)
                if not called and not helper:
                    uncalled.append(f"{cls.name}.{fn.name} ({attr})")
    assert uncalled == [], (
        f"private methods sending listed operations that the CLI never calls: {uncalled}"
    )
