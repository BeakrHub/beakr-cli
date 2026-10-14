"""beakr-bench: public benchmarks for knowledge that compounds across agents.

Four suites, each run only through Beakr's public API (``beakr_sdk``) against a
sandbox organization:

- ``compounding``: the same questions asked after each episode of new material;
  the score that matters is the slope, not the first answer.
- ``corrections``: a value is stated, then corrected; answers must use the
  correction and not the stale value.
- ``false_completeness``: enumeration questions over a deliberately partial
  record; answers must not claim to be complete or invent missing items.
- ``cross_agent_handoff``: a correction made in a Claude Code or Codex session is
  imported and compounded, then a later question must use and cite it.

Scoring is deterministic (``beakr_bench.scoring``). An LLM judge can be plugged in
(``beakr_bench.judge``) but is off by default.
"""

from __future__ import annotations

__version__ = "0.1.0"

SUITES = ("compounding", "corrections", "false_completeness", "cross_agent_handoff")
