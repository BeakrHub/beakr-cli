"""A result whose coverage names the next window has more after it.

Root cause guarded: ``finish`` defaulted ``has_more`` to False whenever the
upstream coverage left it out. Knowledge reads report ``next_offset`` and
``complete`` but no ``has_more``, so a proposals listing on a box read
``{"complete": false, "has_more": false, "next_offset": 10}``. The engine's
``formatting.finish`` (engine #1687) and this copy now take ``has_more`` from
``next_cursor`` / ``next_offset`` when it is absent.
"""

from __future__ import annotations

from beakr_cli import mcp_result


def _coverage(coverage: dict) -> dict:
    return mcp_result.finish({"content": "rows", "coverage": coverage}, tool="knowledge_base")[
        "coverage"
    ]


def test_a_next_offset_means_more_follow():
    coverage = _coverage({"complete": False, "next_offset": 10})
    assert coverage["has_more"] is True
    assert coverage["complete"] is False


def test_a_next_cursor_means_more_follow():
    coverage = _coverage({"next_cursor": "c2"})
    assert coverage["has_more"] is True
    assert coverage["complete"] is False


def test_no_next_window_means_none_follow():
    coverage = _coverage({"returned": 3})
    assert coverage["has_more"] is False
    assert coverage["complete"] is True


def test_a_has_more_the_upstream_states_is_kept():
    assert _coverage({"has_more": False, "next_offset": 10})["has_more"] is False
