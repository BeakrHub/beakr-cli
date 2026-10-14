"""Run beakr-bench from a beakr-cli checkout: ``uv run python benchmarks/run.py --dry-run``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from beakr_bench.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
