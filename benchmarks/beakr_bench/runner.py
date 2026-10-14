"""Run cases against Beakr through the public SDK.

Each case gets its own fresh project in the sandbox org, so cases never see each
other's material. Seeded pages go through the Knowledge write door like any
other write: they stage proposals, and the runner accepts them (a sandbox has no
reviewer). Imported conversations go through import and compound, exactly the
path an outside agent's hooks use, and the runner waits for compounding to
settle before asking anything.

Only the public API is used, so a run measures what a customer's agent would get.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from beakr_bench.cases import Case
from beakr_bench.extract import answer_sources, answer_text, compound_status, proposal_ids
from beakr_bench.judge import Judge, run_judge
from beakr_bench.scoring import score_answer

#: ``imported_conversations.compound_status`` values that end a compound run. The
#: engine records a finished capture as ``done`` (``completed`` is accepted for
#: older engines); ``done`` and ``partial`` mean material was compounded.
SETTLED_COMPOUND_STATUSES = frozenset({"done", "completed", "partial", "skipped", "failed"})
COMPOUNDED_STATUSES = frozenset({"done", "completed", "partial"})


@dataclass
class RunOptions:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    compound_timeout_s: float = 600.0
    poll_interval_s: float = 5.0
    keep_projects: bool = False
    judge: Judge | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


@dataclass
class AskRecord:
    label: str
    question: str
    via: str
    episode: int | None
    passed: bool
    checks: dict[str, bool]
    details: dict[str, Any]
    answer: str
    source_count: int
    judge: dict | None = None


@dataclass
class CaseRecord:
    suite: str
    case_id: str
    title: str
    project_id: str | None = None
    asks: list[AskRecord] = field(default_factory=list)
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.error is None and bool(self.asks) and all(a.passed for a in self.asks)


def _citation(key: str, excerpt: str, title: str) -> dict[str, Any]:
    return {
        "key": key,
        "source_type": "conversation",
        "source_title": title,
        "stance": "support",
        "meta": {"excerpt": excerpt[:500]},
    }


def _cited_sections(case: Case, step_index: int, step: dict[str, Any]) -> list[dict[str, Any]]:
    """Sections with a benchmark source cited on each, as the write door requires."""
    sections = []
    for number, section in enumerate(step["sections"]):
        key = f"conversation:bench-{case.case_id}-{step_index}-{number}"
        title = f"beakr-bench {case.suite}/{case.case_id}"
        if step.get("source_date"):
            title += f" ({step['source_date']})"
        body = f"{section['body']} {{{{{key}}}}}"
        sections.append(
            {
                "title": section["title"],
                "body": body,
                "citations": [_citation(key, section["body"], title)],
            }
        )
    return sections


class CaseRunner:
    def __init__(self, client: Any, options: RunOptions) -> None:
        self.client = client
        self.options = options

    # -- steps -----------------------------------------------------------------

    def _accept_all(self, response: Any, record: CaseRecord) -> None:
        ids = proposal_ids(response)
        if not ids and record.project_id:
            # The public write door reports a staged write in prose and its
            # outcome sidecar, without the proposal's id; the project's pending
            # proposals are the ones this run just staged (a sandbox has no
            # other writer), so accept those.
            listed = self.client.knowledge.proposals.list(project_id=record.project_id) or []
            ids = [
                str(p.get("proposal_id") or p.get("id"))
                for p in listed
                if isinstance(p, dict) and (p.get("proposal_id") or p.get("id"))
            ]
        for proposal_id in ids:
            try:
                self.client.knowledge.proposals.accept(proposal_id)
            except Exception as exc:  # already applied by an auto-approve policy
                record.notes.append(f"accept {proposal_id[:8]}: {type(exc).__name__}")

    def _seed_page(self, case: Case, index: int, step: dict, record: CaseRecord) -> None:
        response = self.client.knowledge.write(
            "new",
            scope=record.project_id,
            title=step["title"],
            page_type=step.get("page_type", "topic"),
            summary=step.get("summary") or step["title"],
            sections=_cited_sections(case, index, step),
            rationale=f"beakr-bench seed for {case.suite}/{case.case_id}",
        )
        self._accept_all(response, record)

    def _edit_page(self, case: Case, index: int, step: dict, record: CaseRecord) -> None:
        response = self.client.knowledge.write(
            "edit",
            scope=record.project_id,
            page=step["title"],
            sections=_cited_sections(case, index, step),
            rationale=step.get("reason") or f"beakr-bench update for {case.case_id}",
        )
        self._accept_all(response, record)

    def _import_conversation(self, case: Case, step: dict, record: CaseRecord) -> None:
        session_id = f"{step['session_id']}-{self.options.run_id}"
        imported = self.client.conversations.import_(
            client=step.get("client", "claude-code"),
            session_id=session_id,
            messages=step["messages"],
            project_id=record.project_id,
            summary=step.get("summary"),
            # The engine keeps message text only by opt-in; hold it for the
            # compound below.
            hold_text=step.get("compound", True),
        )
        conversation_id = imported.get("conversation_id") if isinstance(imported, dict) else None
        if not conversation_id:
            raise RuntimeError("import returned no conversation_id")
        if not step.get("compound", True):
            return
        answer = self.client.engine.compound(conversation_id)
        if isinstance(answer, dict) and answer.get("status") == "nothing_to_compound":
            raise RuntimeError("the engine holds no unread text to compound")
        deadline = self.options.clock() + self.options.compound_timeout_s
        status: str | None = None
        while self.options.clock() < deadline:
            status = compound_status(self.client.conversations.get(conversation_id))
            if status in SETTLED_COMPOUND_STATUSES:
                break
            self.options.sleep(self.options.poll_interval_s)
        record.notes.append(f"compound {session_id}: {status or 'pending'}")
        if status not in COMPOUNDED_STATUSES:
            raise RuntimeError(f"compounding did not complete ({status or 'timed out'})")
        # Compounding stages proposals; a sandbox has no reviewer, so apply them.
        listed = self.client.knowledge.proposals.list(project_id=record.project_id) or []
        ids = [p.get("proposal_id") or p.get("id") for p in listed if isinstance(p, dict)]
        self._accept_all([{"proposal_id": str(i)} for i in ids if i], record)

    def _ask(self, step: dict, record: CaseRecord) -> None:
        via = step.get("via", "research")
        if via == "context":
            response = self.client.engine.context(
                project_id=record.project_id, question=step["question"], format="markdown"
            )
        else:
            response = self.client.knowledge.research(
                step["question"], project_id=record.project_id
            )
        answer = answer_text(response)
        sources = answer_sources(response)
        result = score_answer(answer, sources, step["checks"])
        record.asks.append(
            AskRecord(
                label=step["label"],
                question=step["question"],
                via=via,
                episode=step.get("episode"),
                passed=result.passed,
                checks=result.checks,
                details=result.details,
                answer=answer,
                source_count=len(sources),
                judge=run_judge(
                    self.options.judge, step["question"], answer, step.get("expectation", "")
                ),
            )
        )

    def apply_step(self, case: Case, index: int, step: dict, record: CaseRecord) -> None:
        """Run one step of a case into ``record`` (its project must already exist)."""
        op = step["op"]
        if op == "seed_page":
            self._seed_page(case, index, step, record)
        elif op == "edit_page":
            self._edit_page(case, index, step, record)
        elif op == "import_conversation":
            self._import_conversation(case, step, record)
        elif op == "ask":
            self._ask(step, record)

    # -- a case ----------------------------------------------------------------

    def run(self, case: Case) -> CaseRecord:
        record = CaseRecord(suite=case.suite, case_id=case.case_id, title=case.title)
        try:
            project = self.client.projects.create(
                f"beakr-bench {case.suite} {case.case_id} {self.options.run_id}",
                description="Created by beakr-bench; safe to delete.",
            )
            record.project_id = str(project["id"])
            for index, step in enumerate(case.steps):
                self.apply_step(case, index, step, record)
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
        finally:
            if record.project_id and not self.options.keep_projects:
                try:
                    self.client.projects.delete(record.project_id)
                except Exception as exc:
                    record.notes.append(f"cleanup: {type(exc).__name__}")
        return record
