"""Find the engine files a test compares against, or say why it can't.

Two kinds of test read the engine:

- Contract tests read files (the public contract, the agent tool surface, the
  hosted MCP renderer...). ``engine_checkout`` finds them in
  ``BEAKR_ENGINE_PATH``, else a sibling ``beakr-engine``, else the committed
  snapshot ``snapshots/beakr-engine``
  (``scripts/refresh_snapshots.py``). This repository's CI points
  ``BEAKR_ENGINE_PATH`` at the snapshot and so needs no access to the engine;
  the engine's CI points it at the engine itself.
- Real-engine tests run the engine's own code in its own ``.venv``.
  ``real_engine`` finds a checkout that has one; the snapshot never counts, so
  in this repository's CI they skip. The engine's CI, which checks this
  repository out beside it, is where they run.

With ``BEAKR_REQUIRE_ENGINE=1`` (set in both CIs) a missing file fails the test
instead of skipping it, so a gate cannot silently stop comparing; for
real-engine tests that applies only when ``BEAKR_ENGINE_PATH`` names a real
checkout.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = _REPO / "snapshots" / "beakr-engine"
_PUBLIC = "docs/api/public-openapi.json"
_INTERNAL = "docs/api/internal-openapi.json"
#: What the snapshot keeps of the internal contract: the allowlisted operations.
_CLI_PRIVATE = "docs/api/cli-private-openapi.json"


def _candidates() -> list[Path]:
    candidates = []
    if os.environ.get("BEAKR_ENGINE_PATH"):
        candidates.append(Path(os.environ["BEAKR_ENGINE_PATH"]))
    candidates += [_REPO.parent / "beakr-engine"]
    return [*candidates, SNAPSHOT]


def _missing(message: str) -> None:
    if os.environ.get("BEAKR_REQUIRE_ENGINE") == "1":
        pytest.fail(message)
    pytest.skip(message)


def is_snapshot(root: Path) -> bool:
    return root.resolve() == SNAPSHOT.resolve()


def engine_checkout(relative: str) -> Path:
    """``relative`` in the engine (or its snapshot); skips, or fails when required, if absent."""
    candidates = _candidates()
    for root in candidates:
        if (root / relative).exists():
            return root / relative
    _missing(f"no engine checkout with {relative} (looked in {[str(c) for c in candidates]})")
    raise AssertionError("unreachable")


def real_engine(relative: str) -> Path:
    """An engine checkout (never the snapshot) with ``relative`` and its ``.venv``.

    Skips when there is none; fails under ``BEAKR_REQUIRE_ENGINE=1`` only when
    ``BEAKR_ENGINE_PATH`` names a real checkout that lacks them.
    """
    for root in _candidates():
        if is_snapshot(root):
            continue
        if (root / relative).exists() and (root / ".venv" / "bin" / "python").exists():
            return root
    message = (
        f"needs a beakr-engine checkout with {relative} and its .venv "
        "(the engine's CI runs this test)"
    )
    configured = os.environ.get("BEAKR_ENGINE_PATH")
    if configured and not is_snapshot(Path(configured)):
        _missing(message)
    pytest.skip(message)
    raise AssertionError("unreachable")


def engine_derived(source: str, derived: str, extract: Callable[[str], str]) -> str:
    """``extract`` of the engine's ``source`` file, or the snapshot's ``derived`` copy of it.

    The snapshot keeps some facts instead of the engine files they come from
    (``scripts/refresh_snapshots.py`` computes them with the same ``extract``).
    """
    candidates = _candidates()
    for root in candidates:
        if (root / source).exists():
            return extract((root / source).read_text())
        if is_snapshot(root) and (root / derived).exists():
            return (root / derived).read_text()
    _missing(f"no engine checkout with {source} (looked in {[str(c) for c in candidates]})")
    raise AssertionError("unreachable")


def refresh_snapshots() -> Any:
    """``scripts/refresh_snapshots.py`` as a module."""
    return _script("refresh_snapshots")


def cli_reachable_contract() -> dict[str, Any]:
    """Every operation the CLI may call: the documented contract plus its allowlisted ones.

    From a real engine the allowlisted part is filtered out of the internal
    contract here; the snapshot holds it already filtered (this repository
    never holds the full internal contract).
    """
    generator = _private_api_generator()
    public_path = engine_checkout(_PUBLIC)
    root = public_path.parents[2]
    source = root / _INTERNAL if (root / _INTERNAL).exists() else engine_checkout(_CLI_PRIVATE)
    public = json.loads(public_path.read_text())
    private = generator.filter_contract(
        json.loads(source.read_text()),
        public,
        generator.read_allowlist(generator.ALLOWLIST.read_text()),
    )
    merged: dict[str, Any] = {**public, "paths": {}, "components": {"schemas": {}}}
    for document in (public, private):
        for path, item in document["paths"].items():
            merged["paths"].setdefault(path, {}).update(item)
        merged["components"]["schemas"].update(document.get("components", {}).get("schemas", {}))
    return merged


def hosted_render() -> Any:
    """The hosted MCP server's renderer (``beakr/api/mcp_remote/render.py``), loaded
    from the engine checkout: the module the stdio text must equal byte for byte."""
    path = engine_checkout("beakr/api/mcp_remote/render.py")
    spec = importlib.util.spec_from_file_location("_hosted_mcp_render", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _private_api_generator() -> Any:
    return _script("generate_private_api")


def _script(name: str) -> Any:
    """``scripts/<name>.py`` as a module (scripts is not a package)."""
    path = _REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_engine_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module
