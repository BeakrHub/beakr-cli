"""``beakr_cli.review_flags`` is the engine's review-flag text, byte for byte.

Both MCP servers answer ``review_options`` and ``resolve_review`` with the same
text, and ``beakr kb review`` prints it too, so the CLI carries a copy of the
engine's ``beakr/api/mcp_remote/review_text.py``. The two repositories share no
package; this fails the moment the copy drifts.
"""

from __future__ import annotations

from pathlib import Path

from tests._engine import engine_checkout

_MIRROR = Path(__file__).resolve().parents[1] / "src" / "beakr_cli" / "review_flags.py"


def test_the_copy_matches_the_engine():
    engine = engine_checkout("beakr/api/mcp_remote/review_text.py").read_text()
    assert _MIRROR.read_text() == engine, (
        "beakr_cli/review_flags.py differs from the engine's review_text.py: copy it again"
    )
