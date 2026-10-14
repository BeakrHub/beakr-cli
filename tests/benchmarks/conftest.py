"""beakr-bench lives in ``benchmarks/``, outside the installed packages."""

from __future__ import annotations

import sys
from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))
