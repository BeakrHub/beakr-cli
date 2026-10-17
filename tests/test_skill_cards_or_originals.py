"""The bundled skill teaches when to compute from original files (SPEC-501).

The spec's bundled skill must cover when to read cards, when to compute from
originals and how to record a finding; the reference taught the first and last
but not the decision to go back to the files.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "beakr"


def test_the_research_reference_says_when_to_compute_from_originals():
    text = (ROOT / "reference" / "research-workflow.md").read_text()
    assert "## Cards or the original files" in text
    for phrase in ("read_file", "beakr files download", "add_finding", "source_ref"):
        assert phrase in text


def test_the_skill_points_agents_at_that_guidance():
    skill = (ROOT / "SKILL.md").read_text()
    assert "research-workflow.md#cards-or-the-original-files" in skill
