"""``get_profile`` prints a profile as the hosted server does (``formatting.profile``).

Root cause (sandbox, 2026-11-14): a profile saved with no name printed
``#  (project profile)``: ``data.get("name", "Untitled")`` defaulted only a
missing name, not an empty one. The engine fixed ``formatting.profile``; the
stdio ``_profile_text`` is the same function and is held to it here whenever an
engine checkout is at hand.
"""

from __future__ import annotations

from typing import Any

import pytest

from beakr_cli.mcp_server import _profile_text
from tests._engine import engine_checkout, is_snapshot

SAMPLES: list[tuple[Any, str]] = [
    ({"name": "", "description": "Assays."}, "project"),
    ({"name": "   ", "goals": ["Track runs, samples"]}, "project"),
    ({"description": "No name at all."}, "org"),
    (
        {"name": "Helios", "industry": "biotech", "core_people": [{"name": "Ana", "role": "PI"}]},
        "org",
    ),
    ({}, "project"),
]


@pytest.mark.parametrize("data", [{"name": ""}, {"name": "  "}, {"name": None}, {}])
def test_a_profile_without_a_name_is_untitled(data):
    shown = _profile_text({**data, "description": "x"}, "project")
    assert shown.splitlines()[0] == "# Untitled (project profile)"


def test_the_profile_text_is_the_engines():
    path = engine_checkout("beakr/api/mcp_remote/formatting.py")
    if is_snapshot(path.parents[3]):
        pytest.skip("the snapshot carries no formatting.py")
    source = path.read_text()
    start = source.index("\ndef profile(")
    end = source.index("\ndef ", start + 1)
    namespace: dict[str, Any] = {"Any": Any}
    exec(source[start:end], namespace)  # noqa: S102 - the engine's own function, read only
    for data, kind in SAMPLES:
        assert _profile_text(data, kind) == namespace["profile"](data, kind), data
