"""Refresh the ``beakr api`` command table from a Beakr engine checkout.

    uv run python scripts/sync_operations.py [ENGINE_PATH]
    uv run python scripts/sync_operations.py --check [ENGINE_PATH]   # exit 1 when stale

ENGINE_PATH defaults to $BEAKR_ENGINE_PATH, then ../beakr-engine, then the committed
snapshot ``snapshots/beakr-engine``.
It reads the engine's committed public contract
(``docs/api/public-openapi.json``) and writes the derived command table to
``src/beakr_cli/data/operations.json`` (see ``beakr_cli.api_manifest``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from beakr_cli.api_manifest import build_manifest

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src/beakr_cli/data/operations.json"
RELATIVE = Path("docs/api/public-openapi.json")


def engine_path(argument: str | None) -> Path:
    candidates = [argument, os.environ.get("BEAKR_ENGINE_PATH")]
    candidates += [
        str(ROOT.parent / "beakr-engine"),
        # The committed snapshot (scripts/refresh_snapshots.py), what CI reads.
        str(ROOT / "snapshots" / "beakr-engine"),
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / RELATIVE).exists():
            return Path(candidate)
    raise SystemExit("No engine checkout with docs/api/public-openapi.json found.")


def render(spec: dict) -> str:
    return json.dumps(build_manifest(spec), indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    positional = [arg for arg in argv if arg != "--check"]
    source = engine_path(positional[0] if positional else None) / RELATIVE
    rendered = render(json.loads(source.read_text()))
    if check:
        if not TARGET.exists() or TARGET.read_text() != rendered:
            print(f"stale: {TARGET.relative_to(ROOT)}; run scripts/sync_operations.py")
            return 1
        print("CLI command table is current")
        return 0
    TARGET.write_text(rendered)
    print(f"wrote {TARGET} from {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
