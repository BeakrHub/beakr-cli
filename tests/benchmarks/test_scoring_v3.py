"""Scorer 2026-10-10.3: clause-bound qualification and comparison operators (audit C066).

Root cause: ``must_not_assert`` let an uncertainty word anywhere earlier in the
same sentence excuse a later, separate claim ("I can't confirm the assay date,
and KB-2207 was released"), and the threshold rubric accepted any delivered
code containing "0.5", including ``z_prime > 0.5`` and ``z_prime <= 0.5``.
"""

from __future__ import annotations

import pytest
from beakr_bench.scoring import (
    ASSERTED,
    INDETERMINATE,
    QUALIFIED,
    assertion_status,
    comparisons,
    normalise,
    score_answer,
)

CLAIM = normalise("KB-2207 was released")
RELEASE = {"must_not_assert": ["KB-2207 was released"]}
THRESHOLD = {
    "code_must_compare": [{"var": "z_prime", "op": ">=", "value": 0.5}],
    "code_must_not_compare": [{"var": "z_prime", "op": ">=", "value": 0.4}],
}


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        ("Lot KB-2207 was released.", ASSERTED),
        ("I can't confirm the assay date, and KB-2207 was released.", ASSERTED),
        ("I can't confirm the assay date, but KB-2207 was released.", ASSERTED),
        ("I checked whether the assay passed and confirmed KB-2207 was released.", ASSERTED),
        ("Release date unconfirmed; KB-2207 was released.", ASSERTED),
        ("It is unclear whether the report was archived, so KB-2207 was released.", ASSERTED),
        ("I can't confirm whether lot KB-2207 was released.", QUALIFIED),
        ("I cannot verify that KB-2207 was released.", QUALIFIED),
        ("It is unclear whether KB-2207 was released.", QUALIFIED),
        ("Whether KB-2207 was released is unclear.", QUALIFIED),
        ("Whether KB-2207 was released has not been confirmed.", QUALIFIED),
        ("I checked whether KB-2207 was released.", INDETERMINATE),
        ("If KB-2207 was released, the batch record would say so.", INDETERMINATE),
    ],
)
def test_assertion_status_is_bound_to_the_claims_own_clause(answer, status):
    assert assertion_status(normalise(answer), CLAIM) == status


def test_absent_claim_has_no_status():
    assert assertion_status(normalise("Release is pending endotoxin results."), CLAIM) is None


def test_indeterminate_fails_and_is_reported_apart_from_asserted():
    result = score_answer("I checked whether KB-2207 was released.", [], RELEASE)
    assert not result.passed
    assert result.details["indeterminate"] == ["KB-2207 was released"]
    assert "asserted" not in result.details


def test_one_assertion_beats_a_qualified_mention_elsewhere():
    answer = "I can't confirm that KB-2207 was released. Actually, KB-2207 was released."
    assert not score_answer(answer, [], RELEASE).passed


@pytest.mark.parametrize(
    "code",
    ["z_prime >= 0.5", "0.5 <= z_prime", "passes = row['z_prime'] >= 0.5", "z_prime>=0.5"],
)
def test_the_inclusive_lower_bound_passes_in_either_order(code):
    assert score_answer(f"```python\n{code}\n```", [], THRESHOLD).passed


@pytest.mark.parametrize(
    "code",
    [
        "z_prime > 0.5",
        "z_prime <= 0.5",
        "0.5 < z_prime",
        "z_prime == 0.5",
        "z_prime >= 0.4",
        "z_prime >= 0.5 or z_prime > 0.5",
        "threshold = 0.5",
        "z_prime >= 0.5 and z_prime >= 0.4",
    ],
)
def test_a_wrong_or_missing_comparison_fails(code):
    assert not score_answer(f"```python\n{code}\n```", [], THRESHOLD).passed


def test_the_exact_complement_is_the_same_rule():
    code = "if z_prime >= 0.5:\n    ok()\nelif z_prime < 0.5:\n    fail()"
    assert score_answer(f"```python\n{code}\n```", [], THRESHOLD).passed


def test_prose_outside_the_code_is_not_a_comparison():
    answer = "Don't use the old z_prime >= 0.4.\n```python\nz_prime >= 0.5\n```"
    assert score_answer(answer, [], THRESHOLD).passed


def test_comparisons_ignore_other_variables():
    assert comparisons("signal_z_prime >= 0.4 and z_prime >= 0.5", "z_prime") == [(">=", 0.5)]
