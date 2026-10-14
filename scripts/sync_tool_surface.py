"""Refresh the vendored agent tool surface from a Beakr engine checkout.

    uv run python scripts/sync_tool_surface.py [ENGINE_PATH]
    uv run python scripts/sync_tool_surface.py --check [ENGINE_PATH]   # exit 1 when stale

ENGINE_PATH defaults to $BEAKR_ENGINE_PATH, then ../beakr-engine, then the committed
snapshot ``snapshots/beakr-engine``.
It copies the engine's committed
``docs/api/agent-tool-surface.json`` to ``src/beakr_cli/data/agent_tool_surface.json``.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src/beakr_cli/data/agent_tool_surface.json"
RELATIVE = Path("docs/api/agent-tool-surface.json")


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
    raise SystemExit("No engine checkout with docs/api/agent-tool-surface.json found.")


def main(argv: list[str]) -> int:
    positional = [arg for arg in argv if arg != "--check"]
    source = engine_path(positional[0] if positional else None) / RELATIVE
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_bytes() != source.read_bytes():
            print(f"stale: {TARGET.relative_to(ROOT)}; run scripts/sync_tool_surface.py")
            return 1
        print("vendored agent tool surface is current")
        return 0
    shutil.copyfile(source, TARGET)
    print(f"copied {source} -> {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
