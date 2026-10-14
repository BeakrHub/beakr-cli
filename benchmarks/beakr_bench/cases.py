"""Case files: what each benchmark case seeds, does and asks.

A suite is a JSON file in ``data/`` holding ``{"suite", "description", "cases"}``.
A case is an ordered list of steps; ``ask`` steps carry the checks that score
them. Every case is synthetic: the organizations, people, compounds and values
are invented for this benchmark.

Step ops:

- ``seed_page``   ``{title, page_type?, summary, sections: [{title, body}], source_date?}``
- ``edit_page``   ``{title, sections: [{title, body}], source_date?, reason}``
- ``import_conversation`` ``{client, session_id, messages: [{role, content}],
  summary?, compound: true}``
- ``ask``         ``{label, question, via: research|context, episode?, checks}``

Checks (all optional; every present check must pass):

- ``must_include``: list of groups; each group is a list of alternatives and
  at least one alternative must appear (case-insensitive; Markdown emphasis and
  code marks are ignored, so ``**30**`` matches ``30``).
- ``must_not_include``: strings that must not appear anywhere (stale or invented
  values). Literal: an occurrence fails whatever surrounds it.
- ``must_not_assert``: opt-in, chosen per case, for questions whose correct
  answer is to decline a claim. A listed claim fails when asserted; one
  directly qualified in its own clause ("I can't confirm whether X was
  released", "whether X was released is unclear") is the cautious answer.
  Clauses end at sentence punctuation and coordinating conjunctions, so
  uncertainty in another clause ("I can't confirm the date, and X was
  released") does not excuse it. An occurrence whose qualification cannot be
  resolved from the text is indeterminate, which fails.
- ``code_must_include`` / ``code_must_not_include``: for tasks whose deliverable
  is code, the same two checks applied to the delivered code only (fenced
  blocks, or the whole answer when it has none). A stale value quoted in prose
  as a warning is not the code the agent wrote; the same value in the code is.
- ``code_must_compare`` / ``code_must_not_compare``: lists of
  ``{"var", "op", "value"}`` that the delivered code must (or must not) state
  as a comparison of ``var``, in either operand order (``z_prime >= 0.5`` or
  ``0.5 <= z_prime``). A different operator at the required value fails.
- ``require_citation``: the answer carries at least one source.
- ``forbid_completeness_claim``: no "this is the complete list" style claim.
- ``require_partial_acknowledgement``: the answer says the record may be
  incomplete.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"

STEP_OPS = frozenset({"seed_page", "edit_page", "import_conversation", "ask"})
ASK_VIAS = frozenset({"research", "context"})
CHECK_KEYS = frozenset(
    {
        "must_include",
        "must_not_include",
        "must_not_assert",
        "code_must_include",
        "code_must_not_include",
        "code_must_compare",
        "code_must_not_compare",
        "require_citation",
        "forbid_completeness_claim",
        "require_partial_acknowledgement",
    }
)


class CaseFileError(ValueError):
    """A case file is malformed."""


@dataclass(frozen=True)
class Case:
    suite: str
    case_id: str
    title: str
    steps: tuple[dict[str, Any], ...]
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def asks(self) -> list[dict[str, Any]]:
        return [step for step in self.steps if step["op"] == "ask"]


def _validate_step(case_id: str, index: int, step: dict[str, Any]) -> None:
    where = f"{case_id} step {index}"
    op = step.get("op")
    if op not in STEP_OPS:
        raise CaseFileError(f"{where}: unknown op {op!r}")
    if op == "seed_page" and not (step.get("title") and step.get("sections")):
        raise CaseFileError(f"{where}: seed_page needs title and sections")
    if op == "edit_page" and not (step.get("title") and step.get("sections")):
        raise CaseFileError(f"{where}: edit_page needs title and sections")
    if op == "import_conversation":
        if not step.get("session_id") or not step.get("messages"):
            raise CaseFileError(f"{where}: import_conversation needs session_id and messages")
        for message in step["messages"]:
            if message.get("role") not in {"user", "assistant"} or not message.get("content"):
                raise CaseFileError(f"{where}: messages need role user|assistant and content")
    if op == "ask":
        if not step.get("label") or not step.get("question"):
            raise CaseFileError(f"{where}: ask needs label and question")
        if step.get("via", "research") not in ASK_VIAS:
            raise CaseFileError(f"{where}: via must be one of {sorted(ASK_VIAS)}")
        unknown = set(step.get("checks", {})) - CHECK_KEYS
        if unknown:
            raise CaseFileError(f"{where}: unknown checks {sorted(unknown)}")
        if not step.get("checks"):
            raise CaseFileError(f"{where}: an ask without checks scores nothing")


def load_suite(name: str, *, data_dir: Path = DATA_DIR) -> tuple[str, list[Case]]:
    """Load and validate one suite. Returns (description, cases)."""
    path = data_dir / f"{name}.json"
    if not path.exists():
        raise CaseFileError(f"no case file for suite {name!r} at {path}")
    raw = json.loads(path.read_text())
    if raw.get("suite") != name:
        raise CaseFileError(f"{path.name}: suite field is {raw.get('suite')!r}, expected {name!r}")
    cases: list[Case] = []
    seen: set[str] = set()
    for item in raw.get("cases", []):
        case_id = item.get("id")
        if not case_id or case_id in seen:
            raise CaseFileError(f"{path.name}: missing or duplicate case id {case_id!r}")
        seen.add(case_id)
        steps = item.get("steps") or []
        for index, step in enumerate(steps):
            _validate_step(case_id, index, step)
        if not any(step["op"] == "ask" for step in steps):
            raise CaseFileError(f"{case_id}: a case needs at least one ask")
        cases.append(
            Case(
                suite=name,
                case_id=case_id,
                title=item.get("title", case_id),
                steps=tuple(steps),
                notes=item.get("notes", ""),
                tags=tuple(item.get("tags", [])),
            )
        )
    if not cases:
        raise CaseFileError(f"{path.name}: no cases")
    return raw.get("description", ""), cases
