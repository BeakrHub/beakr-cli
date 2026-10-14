"""``beakr_cli.mcp_result`` is the engine's result helpers, byte for byte (SPEC-488).

Both MCP servers must answer the same ``{content, coverage, next}`` dict, so the
stdio server carries a copy of the shared part of the engine's
``beakr/api/mcp_remote/formatting.py``: ``bounded``, the text limits and cut
hints, ``finish``, ``file_window``, ``page_result`` and ``capped_result``. The two
repositories share no package; this fails the moment the copy drifts.
"""

from __future__ import annotations

from pathlib import Path

from tests._engine import engine_derived, refresh_snapshots

_MIRROR = Path(__file__).resolve().parents[1] / "src" / "beakr_cli" / "mcp_result.py"


def test_the_copy_matches_the_engine():
    # The engine's file, else (in CI) the snapshot's copy of just these helpers.
    snapshots = refresh_snapshots()
    expected = engine_derived(
        snapshots.FORMATTING, snapshots.MCP_RESULT_HELPERS, snapshots.mcp_result_helpers
    ).rstrip()
    mirror = _MIRROR.read_text()
    copied = mirror[mirror.index("def bounded(") :].rstrip()
    assert copied == expected, (
        "beakr_cli/mcp_result.py differs from the engine's formatting.py helpers: copy them again"
    )
