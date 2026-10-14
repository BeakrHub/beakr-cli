"""Live benchmark answers must not fail solely on formatting or explicit uncertainty."""

import json
from pathlib import Path

import pytest
from beakr_bench.scoring import score_answer


def _checks(suite, case_id, label):
    path = Path(__file__).resolve().parents[2] / "benchmarks/beakr_bench/data" / f"{suite}.json"
    case = next(c for c in json.loads(path.read_text())["cases"] if c["id"] == case_id)
    return next(s["checks"] for s in case["steps"] if s.get("label") == label)


@pytest.mark.parametrize("formatted", [False, True])
def test_correct_pipeline_quality_accepts_markdown_emphasis(formatted):
    value = "**30**" if formatted else "30"
    answer = f"The pipeline uses a minimum base quality of {value} (Phred). {{{{wiki:source}}}}"
    checks = _checks("cross_agent_handoff", "codex-correction", "research")
    # Actual suite295f5e38 rejected the correct bold value as missing.
    assert score_answer(answer, [], checks).passed


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Lot KB-2207 was released.", False),
        (
            "I can't confirm whether lot KB-2207 was released. "
            "The record does not report the test results or release decision.",
            True,
        ),
        (
            "The sterility test passed, but I can't confirm that lot KB-2207 was released: "
            "release also required an endotoxin result, which I did not find.",
            True,
        ),
    ],
)
def test_release_claim_guard_distinguishes_assertions_from_uncertainty(answer, expected):
    # Literal forbidden-substring matching currently rejects an explicit
    # refusal to claim release, exactly the cautious behavior the task expects.
    checks = _checks("compounding", "kestrel-batch-release", "e1-release")
    assert score_answer(answer, [], checks).passed is expected


@pytest.mark.parametrize(
    "answer",
    [
        "I can't confirm the assay date, but KB-2207 was released.",
        "Whether the report is archived is unclear, but KB-2207 was released.",
        "Release date unconfirmed; KB-2207 was released.",
    ],
)
def test_unrelated_uncertainty_does_not_excuse_an_asserted_release(answer):
    # Uncertainty must qualify the prohibited assertion itself. A cue anywhere
    # earlier in the sentence incorrectly forgives an explicit release claim.
    checks = _checks("compounding", "kestrel-batch-release", "e1-release")
    assert not score_answer(answer, [], checks).passed


def test_literal_forbidden_marker_is_not_relaxed_by_uncertainty():
    # Literal exclusions remain literal; cases needing semantic assertions
    # should select an explicit assertion-aware check instead.
    checks = {"must_not_include": ["FORBIDDEN_MARKER"]}
    assert not score_answer("I can't confirm FORBIDDEN_MARKER.", [], checks).passed


@pytest.mark.parametrize(
    "answer",
    [
        "I can't confirm the assay date, and KB-2207 was released.",
        "I checked whether the assay passed and confirmed KB-2207 was released.",
    ],
)
def test_a_separate_assertion_is_not_qualified_by_an_earlier_question(answer):
    # A different conjunction or a question mentioned as something already
    # checked does not make the subsequent release assertion uncertain.
    checks = _checks("compounding", "kestrel-batch-release", "e1-release")
    assert not score_answer(answer, [], checks).passed


@pytest.mark.parametrize("condition", ["z_prime <= 0.5", "z_prime > 0.5"])
def test_threshold_rubric_rejects_incorrect_comparison(condition):
    # The required deliverable is an inclusive lower-bound QC condition.
    # Merely mentioning 0.5 in code does not establish correctness.
    path = Path(__file__).resolve().parents[2] / "benchmarks/beakr_bench/data/agent_onboarding.json"
    case = next(
        c for c in json.loads(path.read_text())["cases"] if c["id"] == "corrected-threshold"
    )
    assert not score_answer(f"```python\n{condition}\n```", [], case["task"]["checks"]).passed


@pytest.mark.parametrize("severity", ["Sev-2", "Sev\u20112"])
def test_onboarding_severity_accepts_equivalent_hyphen_typography(severity):
    # The live v2 API answer uses U+2011, a nonbreaking hyphen, but gives
    # the correct severity and recipient. Typography does not change severity.
    path = (
        Path(__file__).resolve().parents[2] / "benchmarks/beakr_bench/data/agent_onboarding_v2.json"
    )
    case = next(c for c in json.loads(path.read_text())["cases"] if c["id"] == "sev2-paging")
    assert score_answer(
        f"Severity: {severity}. Page Priya Nandakumar, the secondary on-call.",
        [],
        case["task"]["checks"],
    ).passed
