"""stdio mirror: the MCP text cap is enforced on the text, never on a flag (audit C077).

Same root cause as the engine: an incoming ``coverage.truncated`` let an
oversized result skip the cap.
"""

from __future__ import annotations

from beakr_cli import mcp_result

LIMIT = mcp_result.TEXT_LIMITS["knowledge_base"]


def test_an_oversized_result_flagged_truncated_upstream_is_still_capped():
    shaped = mcp_result.finish(
        {"content": "x" * 90_000, "coverage": {"complete": False, "truncated": True}},
        tool="knowledge_base",
    )
    assert len(shaped["content"]) <= LIMIT + 64
    assert shaped["coverage"]["complete"] is False
    assert shaped["coverage"]["truncated"] is True


def test_a_result_this_cap_already_cut_is_left_unchanged():
    once = mcp_result.finish({"content": "x" * 90_000}, tool="knowledge_base")
    assert mcp_result.finish(once, tool="knowledge_base") == once


def test_an_incomplete_upstream_result_that_fits_stays_incomplete_and_uncut():
    shaped = mcp_result.finish(
        {"content": "short", "coverage": {"complete": False, "truncated": True}},
        tool="knowledge_base",
    )
    assert shaped["content"] == "short"
    assert shaped["coverage"]["complete"] is False
