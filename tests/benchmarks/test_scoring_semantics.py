"""Scoring semantics after C066: literal exclusions stay literal; opt-in checks are explicit.

Root cause: live answers failed for formatting (``**30**``), for explicitly
declining a claim ("I can't confirm whether KB-2207 was released"), and for
warning against a stale value in prose while delivering code with the current
one. The fixes must not weaken literal exclusion or let an asserted claim pass.
"""

from __future__ import annotations

import pytest
from beakr_bench.onboarding.cases import load_cases
from beakr_bench.scoring import delivered_code, score_answer


def _threshold_checks():
    _, cases = load_cases()
    return next(c for c in cases if c.case_id == "corrected-threshold").checks


@pytest.mark.parametrize(
    "answer",
    ["The Kd is 38 nM.", "The Kd is **38** nM.", "The Kd is `38 nM`.", "I can't confirm 38 nM."],
)
def test_literal_exclusion_is_never_weaker(answer):
    assert not score_answer(answer, [], {"must_not_include": ["38 nM"]}).passed


def test_formatting_is_ignored_only_for_positive_matching():
    assert score_answer("Use **52 nM**.", [], {"must_include": [["52 nM"]]}).passed


@pytest.mark.parametrize(
    ("answer", "passes"),
    [
        # The deliverable uses the current value; the stale one only in a prose warning.
        ("```python\nz_prime >= 0.5\n```\nDon't reuse the old `z_prime >= 0.4`.", True),
        # Stale value in the delivered code fails, even beside a correct block.
        ("```python\nz_prime >= 0.4\n```", False),
        ("```python\nok = z_prime >= 0.5\nlegacy = z_prime >=0.4\n```", False),
        # No fenced code: the whole answer is the delivered code.
        ("z_prime >= 0.4", False),
        ("z_prime >= 0.5", True),
        # Code without the current value fails even if prose names it.
        ("The threshold is 0.5.\n```python\nz_prime > 0\n```", False),
    ],
)
def test_threshold_case_scores_the_delivered_code(answer, passes):
    assert score_answer(answer, [], _threshold_checks()).passed is passes


def test_delivered_code_is_fenced_blocks_or_the_whole_answer():
    assert delivered_code("x\n```py\na = 1\n```\ny\n```\nb = 2\n```") == "a = 1\n\nb = 2\n"
    assert delivered_code("a = 1") == "a = 1"


@pytest.mark.parametrize(
    ("answer", "passes"),
    [
        ("I could not verify that KB-2207 was released.", True),
        ("It is unclear whether KB-2207 was released; the endotoxin result is pending.", True),
        ("KB-2207 was released on 2026-05-20.", False),
        ("The endotoxin result is pending, however KB-2207 was released.", False),
        ("I don't know the date. KB-2207 was released.", False),
    ],
)
def test_must_not_assert_needs_uncertainty_in_the_claims_own_clause(answer, passes):
    checks = {"must_not_assert": ["KB-2207 was released"]}
    assert score_answer(answer, [], checks).passed is passes
