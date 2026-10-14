"""Optional LLM judge hook. Off by default.

The deterministic checks are the score. A judge is useful for reading results
(does a passing answer actually say what the case wanted, or did it pass on a
lucky substring?), so a run can attach one, but its verdict is recorded beside
the deterministic result and never replaces it.

A judge is any callable ``(question, answer, expectation) -> JudgeVerdict``.
``beakr-bench`` ships no default model client on purpose: pick the model and
provider yourself and pass it with ``--judge module:callable``.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class JudgeVerdict:
    passed: bool | None  # None: the judge could not decide (unreachable, refused)
    rationale: str = ""


Judge = Callable[[str, str, str], JudgeVerdict]


def load_judge(spec: str | None) -> Judge | None:
    """``"package.module:function"`` -> the callable; ``None`` keeps judging off."""
    if not spec:
        return None
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError("--judge must look like 'package.module:function'")
    judge = getattr(importlib.import_module(module_name), attr)
    if not callable(judge):
        raise ValueError(f"{spec} is not callable")
    return judge


def run_judge(judge: Judge | None, question: str, answer: str, expectation: str) -> dict | None:
    if judge is None:
        return None
    try:
        verdict = judge(question, answer, expectation)
    except Exception as exc:  # a judge failure says nothing about the system under test
        return {"passed": None, "rationale": f"judge error: {type(exc).__name__}"}
    return {"passed": verdict.passed, "rationale": verdict.rationale}
