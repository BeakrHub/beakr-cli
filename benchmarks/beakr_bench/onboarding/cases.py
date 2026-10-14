"""Onboarding case files: what each case seeds and the task the agent is given.

``data/agent_onboarding.json`` holds ``{"suite", "description", "cases"}``. A case is::

    {"id", "title", "setup": [seed_page | edit_page steps], "task": {"prompt", "checks"}}

Setup steps use the same ops and fields as the other suites (``beakr_bench.cases``)
and go through the same Knowledge write door. ``task.checks`` uses the shared
deterministic checks (``beakr_bench.scoring``). Every case is synthetic, and an
honest agent must be able to pass it from what the setup seeded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beakr_bench.cases import CHECK_KEYS, DATA_DIR, Case, CaseFileError, _validate_step
from beakr_bench.onboarding import SUITE

SETUP_OPS = frozenset({"seed_page", "edit_page"})


@dataclass(frozen=True)
class OnboardingCase:
    case_id: str
    title: str
    setup: tuple[dict[str, Any], ...]
    prompt: str
    checks: dict[str, Any]
    notes: str = ""

    def as_setup_case(self) -> Case:
        """The setup as a ``beakr_bench`` case, so the shared runner seeds it."""
        return Case(suite=SUITE, case_id=self.case_id, title=self.title, steps=self.setup)


def load_cases(
    *, data_dir: Path = DATA_DIR, path: Path | None = None
) -> tuple[str, list[OnboardingCase]]:
    """Load the default case file, or ``path`` (a pre-registered variant set)."""
    path = path or data_dir / f"{SUITE}.json"
    raw = json.loads(path.read_text())
    if raw.get("suite") != SUITE:
        raise CaseFileError(f"{path.name}: suite field is {raw.get('suite')!r}, expected {SUITE!r}")
    cases: list[OnboardingCase] = []
    seen: set[str] = set()
    for item in raw.get("cases", []):
        case_id = item.get("id")
        if not case_id or case_id in seen:
            raise CaseFileError(f"{path.name}: missing or duplicate case id {case_id!r}")
        seen.add(case_id)
        setup = item.get("setup") or []
        if not setup:
            raise CaseFileError(f"{case_id}: a case needs setup steps to seed its knowledge")
        for index, step in enumerate(setup):
            if step.get("op") not in SETUP_OPS:
                raise CaseFileError(
                    f"{case_id} setup {index}: op must be one of {sorted(SETUP_OPS)}"
                )
            _validate_step(case_id, index, step)
        task = item.get("task") or {}
        if not task.get("prompt"):
            raise CaseFileError(f"{case_id}: task needs a prompt")
        checks = task.get("checks") or {}
        if not checks:
            raise CaseFileError(f"{case_id}: a task without checks scores nothing")
        unknown = set(checks) - CHECK_KEYS
        if unknown:
            raise CaseFileError(f"{case_id}: unknown checks {sorted(unknown)}")
        cases.append(
            OnboardingCase(
                case_id=case_id,
                title=item.get("title", case_id),
                setup=tuple(setup),
                prompt=task["prompt"],
                checks=checks,
                notes=item.get("notes", ""),
            )
        )
    if not cases:
        raise CaseFileError(f"{path.name}: no cases")
    return raw.get("description", ""), cases
