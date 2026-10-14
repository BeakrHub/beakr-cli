"""Run the live cross-agent handoff (EVAL-572) from a beakr-cli checkout.

    BEAKR_API_KEY=bk_test_... BEAKR_API_URL=https://... \
        uv run python benchmarks/run_live_handoff.py --out results/
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from beakr_bench.live_handoff import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
