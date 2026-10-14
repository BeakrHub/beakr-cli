"""Deterministic checks for one answer.

Each check is a pure function of the answer text and its sources, so a run can
be re-scored from its stored report without calling Beakr again. Matching is
case-insensitive and whitespace-normalised; numbers and units are matched as
written in the case file (cases spell the forms they accept as alternatives).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# "Here is the complete list", "these are all", "the only three", "every X is".
_COMPLETENESS_CLAIMS = (
    r"\b(this|that|these|those) (is|are) (the )?(complete|full|entire|exhaustive)\b",
    r"\b(complete|full|exhaustive|entire) (list|set|record|inventory)\b",
    r"\b(these|those) are (all|the only)\b",
    r"\bthe only (\w+ ){0,3}(are|is|were|was)\b",
    r"\bthere (are|were) (exactly|only) \w+\b",
    r"\bno other\b",
    r"\ball (of )?the \w+ (are|were) (listed|covered|included)\b",
)

# "may not be complete", "the record only covers", "others may exist".
_PARTIAL_ACKNOWLEDGEMENTS = (
    r"\b(may|might|could) (not )?be (incomplete|missing|more)\b",
    r"\bnot (necessarily )?(complete|exhaustive|comprehensive)\b",
    r"\bincomplete\b",
    r"\b(only|just) (covers?|includes?|shows?|has|have|found|records?)\b",
    r"\b(there )?(may|might|could) be (more|others|additional)\b",
    r"\bbased on (what|the (records?|sources?|pages?|information)) "
    r"(is |are )?(available|recorded|found)\b",
    r"\b(coverage|record|records) (is|are) (partial|limited|incomplete)\b",
    r"\bI (could only|only) find\b",
    r"\bno (records?|information|data|sources?) (for|on|about)\b",
)

_CITATION_MARKERS = re.compile(r"\{\{[^}]+\}\}|\[\[[^\]]+\]\]|\[\^?\d+\]|\[source:", re.I)


# Markdown emphasis and code marks around a value ("**30**", "`PBS-T`") are
# formatting, not content: a correct bold answer used to fail must_include.
_MARKDOWN_MARKS = re.compile(r"\*\*|__|\*|`")

# ``must_not_assert``: a forbidden claim counts unless the answer declines to
# make it, and the decline must govern that very claim. Each occurrence is judged
# in its own clause; clauses end at sentence punctuation and at coordinating
# conjunctions ("and", "but", "so", ...), so "I can't confirm the assay date, and
# KB-2207 was released" asserts the release. Within the clause, only a cue that
# directly scopes the claim qualifies it:
#
# - a declining verb right before it: "I can't confirm (that|whether|if) KB-2207 was released";
# - an uncertainty word right before it: "it is unclear whether KB-2207 was released";
# - an open "whether/if" question about it that the clause then calls unresolved:
#   "whether KB-2207 was released is unclear".
#
# Anything else that still carries an uncertainty word ("I checked whether the
# assay passed ... KB-2207 was released") cannot be resolved from text, so it is
# INDETERMINATE: reported separately and never counted as a pass (audit C066).
_CLAUSE_BREAK = re.compile(
    r"[.!?;:\n]|\b(?:and|but|so|yet|however|though|although|while|whereas|then)\b"
)
_DECLINE = (
    r"(?:can(?:not|'t|’t)|could(?:n't|n’t| not)|unable to|do(?:n't|n’t| not)|"
    r"did(?:n't|n’t| not)|not able to|have not|haven't|haven’t|has not|hasn't|hasn’t)"
    r"\s+(?:yet\s+)?(?:be(?:en)?\s+)?(?:confirm(?:ed)?|verif(?:y|ied)|say|tell|know|establish(?:ed)?)"
)
_UNCERTAIN_WORD = (
    r"(?:unclear|unknown|unconfirmed|uncertain|not (?:yet )?(?:confirmed|recorded|known))"
)
_SCOPE_PREFIX = re.compile(
    rf"(?:{_DECLINE}|{_UNCERTAIN_WORD})\s+(?:that|whether|if)?\s*(?:lot\s+)?$"
)
_OPEN_QUESTION_PREFIX = re.compile(r"(?:^|\s)(?:whether|if)\s+(?:lot\s+)?$")
_UNRESOLVED_SUFFIX = re.compile(
    rf"^\s*(?:(?:is|was|remains)\s+(?:still\s+)?{_UNCERTAIN_WORD}"
    r"|(?:has not been|hasn't been|hasn’t been|has yet to be|is not yet|was not)\s+confirmed)\b"
)
_ANY_UNCERTAINTY = re.compile(
    rf"\b(?:whether|if|{_UNCERTAIN_WORD}|not sure|unsure|cannot|can't|can’t|couldn't|unable)\b"
    rf"|{_DECLINE}"
)

ASSERTED = "asserted"
QUALIFIED = "qualified"
INDETERMINATE = "indeterminate"


#: Bumped whenever a check's meaning changes, so a rescored report names the
#: rules it was scored with (``beakr_bench.rescore``).
SCORER_VERSION = "2026-10-10.4"
#: Why the version last changed (recorded in rescored reports' provenance).
SCORER_CHANGE = (
    "must_include (human text only) folds typographic equivalents before matching: Unicode "
    "hyphens and dashes (U+2010-2015, U+2212, U+FE58, U+FE63, U+FF0D) to '-', curly quotes to "
    "straight, and non-breaking/narrow spaces to a space, so 'Sev\u20112' matches 'Sev-2' "
    "(audit C068). Literal exclusions and code_* checks are unchanged."
)

# Fenced code blocks ("```python ... ```"). For a task whose deliverable is code,
# ``code_*`` checks read the delivered code: a stale value quoted in prose as a
# warning ("don't reuse the old ``>= 0.4``") is not the code the agent wrote.
_FENCED_CODE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def plain(text: str) -> str:
    """Case- and whitespace-normalised text, formatting kept: literal checks use it."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def normalise(text: str) -> str:
    """``plain`` with Markdown emphasis and code marks removed (positive matching)."""
    return plain(_MARKDOWN_MARKS.sub("", text or ""))


# Typographic equivalents a human-text answer may use for the same characters.
# Folded only for positive human-text matching (``must_include``): a literal
# exclusion stays literal, and delivered code keeps its exact characters (C068).
_TYPOGRAPHY = str.maketrans(
    {
        **{chr(c): "-" for c in (*range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D)},
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "\u2009": " ",
        "\u200a": " ",
    }
)


def normalise_text(text: str) -> str:
    """``normalise`` plus typographic folding: for human-text ``must_include`` only."""
    return normalise((text or "").translate(_TYPOGRAPHY))


def delivered_code(answer: str) -> str:
    """The code an answer delivers: its fenced blocks, or the whole answer if it has none."""
    blocks = _FENCED_CODE.findall(answer or "")
    return "\n".join(blocks) if blocks else (answer or "")


@dataclass
class CheckResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


def _includes(
    text: str, groups: list[Any], fold: Callable[[str], str] = normalise
) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for group in groups:
        alternatives = group if isinstance(group, list) else [group]
        if not any(fold(alt) in text for alt in alternatives):
            missing.append(" | ".join(str(a) for a in alternatives))
    return not missing, missing


def _excludes(answer: str, forbidden: list[str]) -> tuple[bool, list[str]]:
    """Literal exclusion: a value fails if it appears as written or with its formatting removed.

    Never weaker than plain substring matching: Markdown around a forbidden value
    (``**38** nM``) does not hide it.
    """
    views = (plain(answer), normalise(answer))
    found = [
        value
        for value in forbidden
        if any(plain(value) in view or normalise(value) in view for view in views)
    ]
    return not found, found


def _clause_around(text: str, start: int, end: int) -> tuple[str, str]:
    """The text of ``start:end``'s clause before and after it."""
    before = [m.end() for m in _CLAUSE_BREAK.finditer(text, 0, start)]
    after = _CLAUSE_BREAK.search(text, end)
    return (
        text[before[-1] if before else 0 : start],
        text[end : after.start() if after else len(text)],
    )


def assertion_status(text: str, phrase: str) -> str | None:
    """How ``phrase`` appears in normalised ``text``: None when absent, else the worst occurrence.

    ``asserted`` beats ``indeterminate`` beats ``qualified``: one plain assertion
    anywhere is an assertion, whatever else the answer says.
    """
    statuses: list[str] = []
    start = text.find(phrase)
    while start != -1:
        prefix, suffix = _clause_around(text, start, start + len(phrase))
        if _SCOPE_PREFIX.search(prefix) or (
            _OPEN_QUESTION_PREFIX.search(prefix) and _UNRESOLVED_SUFFIX.search(suffix)
        ):
            statuses.append(QUALIFIED)
        elif _ANY_UNCERTAINTY.search(prefix):
            statuses.append(INDETERMINATE)
        else:
            statuses.append(ASSERTED)
        start = text.find(phrase, start + 1)
    for status in (ASSERTED, INDETERMINATE, QUALIFIED):
        if status in statuses:
            return status
    return None


def _not_asserted(text: str, claims: list[str]) -> tuple[bool, list[str], list[str]]:
    asserted: list[str] = []
    indeterminate: list[str] = []
    for value in claims:
        status = assertion_status(text, normalise(value))
        if status == ASSERTED:
            asserted.append(value)
        elif status == INDETERMINATE:
            indeterminate.append(value)
    return not asserted and not indeterminate, asserted, indeterminate


# ``code_must_compare`` / ``code_must_not_compare``: the comparisons the
# delivered code makes on a named variable, both ``z_prime >= 0.5`` and
# ``0.5 <= z_prime`` forms. Mentioning the value is not the rule: ``z_prime > 0.5``
# and ``z_prime <= 0.5`` both contain "0.5" and both are wrong for an inclusive
# lower bound.
_OPS = ("<=", ">=", "==", "!=", "<", ">")
_FLIPPED = {"<=": ">=", ">=": "<=", "<": ">", ">": "<", "==": "==", "!=": "!="}
_COMPLEMENT = {">=": "<", "<": ">=", "<=": ">", ">": "<=", "==": "!=", "!=": "=="}
_NUMBER = r"-?\d+(?:\.\d+)?"
_OPERAND = r"[A-Za-z_][\w.\[\]'\"]*"
_FORWARD = re.compile(rf"({_OPERAND})\s*(<=|>=|==|!=|<|>)\s*({_NUMBER})")
_BACKWARD = re.compile(rf"({_NUMBER})\s*(<=|>=|==|!=|<|>)\s*({_OPERAND})")


def comparisons(code: str, var: str) -> list[tuple[str, float]]:
    """``(op, value)`` for every comparison of ``var`` with a number, as ``var op value``."""
    named = re.compile(rf"(?:^|[^\w]){re.escape(var)}(?:[^\w]|$)")
    found: list[tuple[str, float]] = []
    for operand, op, number in _FORWARD.findall(code or ""):
        if named.search(f" {operand} "):
            found.append((op, float(number)))
    for number, op, operand in _BACKWARD.findall(code or ""):
        if named.search(f" {operand} "):
            found.append((_FLIPPED[op], float(number)))
    return found


def _compares(code: str, rule: dict[str, Any]) -> bool:
    """The code states ``var op value`` and never contradicts it at that value.

    Its exact complement (``z_prime < 0.5`` beside ``z_prime >= 0.5``) is the
    same rule from the other side; any other operator at the same value is a
    different rule.
    """
    var, op, value = rule["var"], rule["op"], float(rule["value"])
    at_value = [o for o, v in comparisons(code, var) if v == value]
    return op in at_value and all(o in (op, _COMPLEMENT[op]) for o in at_value)


def _never_compares(code: str, rule: dict[str, Any]) -> bool:
    var, op, value = rule["var"], rule["op"], float(rule["value"])
    return (op, value) not in comparisons(code, var)


def claims_completeness(text: str) -> list[str]:
    return [p for p in _COMPLETENESS_CLAIMS if re.search(p, text, re.I)]


def acknowledges_partial(text: str) -> bool:
    return any(re.search(p, text, re.I) for p in _PARTIAL_ACKNOWLEDGEMENTS)


def has_citation(text: str, sources: list[Any]) -> bool:
    return bool(sources) or bool(_CITATION_MARKERS.search(text or ""))


def score_answer(answer: str, sources: list[Any], checks: dict[str, Any]) -> CheckResult:
    """Run every check present in ``checks``; the answer passes when all pass.

    - ``must_include``: each group needs one alternative present, ignoring
      Markdown emphasis and code marks (``**30**`` is ``30``) and typographic
      equivalents (``Sev\u20112`` is ``Sev-2``).
    - ``must_not_include``: literal. Any occurrence fails, whatever surrounds it
      (case and whitespace normalised only).
    - ``must_not_assert``: opt-in, per case. A listed claim passes only when
      absent or directly qualified in its own clause; an assertion fails, and an
      occurrence whose qualification cannot be resolved is ``indeterminate``
      (``details["indeterminate"]``), which also fails.
    - ``code_must_include`` / ``code_must_not_include``: the same as the first
      two, applied to the delivered code only (``delivered_code``).
    - ``code_must_compare`` / ``code_must_not_compare``: lists of
      ``{"var", "op", "value"}``. The delivered code must state (or must not
      state) that comparison of ``var``, in either operand order; a different
      operator at the required value fails.
    """
    text = normalise(answer)
    literal = plain(answer)
    result = CheckResult(passed=True)
    if "must_include" in checks:
        ok, missing = _includes(normalise_text(answer), checks["must_include"], normalise_text)
        result.checks["must_include"] = ok
        if missing:
            result.details["missing"] = missing
    if "must_not_include" in checks:
        ok, found = _excludes(answer, checks["must_not_include"])
        result.checks["must_not_include"] = ok
        if found:
            result.details["forbidden_present"] = found
    code_checks = ("code_must_include", "code_must_not_include", "code_must_compare")
    if any(key in checks for key in (*code_checks, "code_must_not_compare")):
        code = delivered_code(answer)
        if "code_must_compare" in checks:
            failed = [r for r in checks["code_must_compare"] if not _compares(code, r)]
            result.checks["code_must_compare"] = not failed
            if failed:
                result.details["code_comparison_missing"] = failed
        if "code_must_not_compare" in checks:
            failed = [r for r in checks["code_must_not_compare"] if not _never_compares(code, r)]
            result.checks["code_must_not_compare"] = not failed
            if failed:
                result.details["code_comparison_forbidden"] = failed
        if "code_must_include" in checks:
            ok, missing = _includes(normalise(code), checks["code_must_include"])
            result.checks["code_must_include"] = ok
            if missing:
                result.details["code_missing"] = missing
        if "code_must_not_include" in checks:
            ok, found = _excludes(code, checks["code_must_not_include"])
            result.checks["code_must_not_include"] = ok
            if found:
                result.details["code_forbidden_present"] = found
    if "must_not_assert" in checks:
        ok, asserted, indeterminate = _not_asserted(text, checks["must_not_assert"])
        result.checks["must_not_assert"] = ok
        if asserted:
            result.details["asserted"] = asserted
        if indeterminate:
            # Not a pass: whether uncertainty covers the claim could not be read
            # from the text. Kept apart from "asserted" so a report can count it.
            result.details["indeterminate"] = indeterminate
    if checks.get("require_citation"):
        result.checks["require_citation"] = has_citation(answer, sources)
    if checks.get("forbid_completeness_claim"):
        claims = claims_completeness(literal)
        result.checks["forbid_completeness_claim"] = not claims
        if claims:
            result.details["completeness_claims"] = claims
    if checks.get("require_partial_acknowledgement"):
        result.checks["require_partial_acknowledgement"] = acknowledges_partial(literal)
    result.passed = all(result.checks.values()) if result.checks else False
    return result
