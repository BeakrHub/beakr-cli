"""Typographic equivalents count only for positive human-text matching (C068).

Root cause: a live answer wrote "Sev‑2" with U+2011 (non-breaking hyphen) and
failed ``must_include: Sev-2`` although the severity was correct. Folding is
limited to ``must_include``; literal exclusions and delivered code stay exact.
"""

from __future__ import annotations

import pytest
from beakr_bench.scoring import normalise_text, score_answer


@pytest.mark.parametrize(
    "variant",
    ["Sev‐2", "Sev‑2", "Sev–2", "Sev—2", "Sev−2", "Sev－2"],
)
def test_unicode_hyphens_match_an_ascii_hyphen(variant):
    assert score_answer(f"Page them at {variant}.", [], {"must_include": ["Sev-2"]}).passed


def test_curly_quotes_and_nbsp_match_their_plain_forms():
    answer = "Use the “PBS T” buffer — it’s required."
    assert score_answer(answer, [], {"must_include": ['"pbs t"', "it's required"]}).passed


def test_literal_exclusions_are_not_folded():
    # A forbidden ASCII value is not matched by a typographic variant, and the
    # check never treats folding as a reason to pass or fail differently.
    assert score_answer("Sev‑2", [], {"must_not_include": ["Sev-2"]}).passed
    assert not score_answer("Sev-2", [], {"must_not_include": ["Sev-2"]}).passed


def test_code_checks_keep_exact_characters():
    code = "```python\nthreshold = 0‑5\n```"
    assert not score_answer(code, [], {"code_must_include": ["0-5"]}).passed


def test_folding_is_idempotent_and_case_insensitive():
    once = normalise_text("SEV‑2 “X”")
    assert normalise_text(once) == once == 'sev-2 "x"'
