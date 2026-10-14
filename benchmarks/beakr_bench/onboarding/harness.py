"""Run onboarding cases: seed a project, fetch the injected context, run both arms, score.

Per case, one fresh sandbox project is created and seeded through the public
API (the shared ``beakr_bench`` runner's write-door seeding). The context the
agent's session hook would inject is fetched once, before either arm runs, so
nothing the without-context arm does (its lookups, a session bookmark) can
change what the with-context arm is given. Then each arm runs the same task
with the same agent and tools, ``repeats`` times, and the final answer is
scored with the shared deterministic checks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from beakr_bench.extract import answer_text
from beakr_bench.onboarding import ARMS
from beakr_bench.onboarding.agents import Agent, AgentRun
from beakr_bench.onboarding.cases import OnboardingCase
from beakr_bench.runner import CaseRecord, CaseRunner, RunOptions
from beakr_bench.scoring import score_answer


@dataclass
class ArmResult:
    arm: str
    repeat: int
    passed: bool
    checks: dict[str, bool]
    details: dict[str, Any]
    knowledge_lookups: int
    turns: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    wall_s: float
    cost_usd: float | None
    error: str | None
    answer: str
    tools: list[dict[str, Any]]
    models_served: list[str]


@dataclass
class OnboardingRecord:
    case_id: str
    title: str
    project_id: str | None = None
    context_chars: int = 0
    arms: list[ArmResult] = field(default_factory=list)
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def score_run(arm: str, repeat: int, run: AgentRun, checks: dict[str, Any]) -> ArmResult:
    # A run that failed before answering is scored as a failed task (the agent
    # could not do it), and the error is kept beside the result.
    result = score_answer(run.answer, [], checks)
    return ArmResult(
        arm=arm,
        repeat=repeat,
        passed=result.passed and run.error is None,
        checks=result.checks,
        details=result.details,
        knowledge_lookups=run.knowledge_lookups,
        turns=run.turns,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cache_read_tokens=run.cache_read_tokens,
        wall_s=run.wall_s,
        cost_usd=run.cost_usd,
        error=run.error,
        answer=run.answer,
        tools=[asdict(call) for call in run.tool_calls],
        models_served=run.models_served,
    )


class OnboardingRunner:
    def __init__(
        self,
        client: Any,
        agent: Agent,
        options: RunOptions,
        *,
        repeats: int = 1,
        evidence: Any = None,
    ):
        self.client = client
        self.agent = agent
        self.options = options
        self.repeats = max(1, repeats)
        self._seeder = CaseRunner(client, options)
        #: An ``EvidenceLog``: each finished run is written to disk as it ends.
        self.evidence = evidence

    def _context(self, project_id: str) -> str:
        # Exactly what the agent's session hook asks for: the project, no
        # question, in that hook's format.
        response = self.client.engine.context(
            project_id=project_id, format=self.agent.context_format
        )
        return answer_text(response).strip()

    def run(self, case: OnboardingCase) -> OnboardingRecord:
        record = OnboardingRecord(case_id=case.case_id, title=case.title)
        seed_record = CaseRecord(suite="agent_onboarding", case_id=case.case_id, title=case.title)
        try:
            project = self.client.projects.create(
                f"beakr-bench onboarding {case.case_id} {self.options.run_id}",
                description="Created by beakr-bench; safe to delete.",
            )
            record.project_id = seed_record.project_id = str(project["id"])
            if self.evidence is not None:
                self.evidence.case_started(case.case_id, case.title, record.project_id)
            setup = case.as_setup_case()
            for index, step in enumerate(setup.steps):
                self._seeder.apply_step(setup, index, step, seed_record)
            record.notes.extend(seed_record.notes)
            context = self._context(record.project_id)
            record.context_chars = len(context)
            if self.evidence is not None:
                self.evidence.context(case.case_id, case.prompt, context, record.notes)
            for repeat in range(self.repeats):
                for arm in ARMS:
                    given = context if arm == "with_context" else None
                    run = self.agent.run(
                        prompt=case.prompt, context=given, project_id=record.project_id
                    )
                    result = score_run(arm, repeat, run, case.checks)
                    record.arms.append(result)
                    if self.evidence is not None:
                        self.evidence.run_finished(
                            case.case_id, prompt=case.prompt, context=given, run=run, result=result
                        )
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
        finally:
            if record.project_id and not self.options.keep_projects:
                try:
                    self.client.projects.delete(record.project_id)
                except Exception as exc:
                    record.notes.append(f"cleanup: {type(exc).__name__}")
            if self.evidence is not None:
                self.evidence.case_finished(record)
        return record
