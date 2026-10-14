"""beakr-bench end to end over mocked HTTP: real SDK, real runner, fake engine."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from beakr_bench import SUITES
from beakr_bench.cases import Case, load_suite
from beakr_bench.cli import main
from beakr_bench.dryrun import FakeEngine, dry_run_client
from beakr_bench.judge import JudgeVerdict
from beakr_bench.report import build_report, render_markdown
from beakr_bench.runner import CaseRunner, RunOptions
from beakr_sdk import Beakr


def _runner(engine: FakeEngine | None = None, **options):
    client, engine = dry_run_client(engine)
    return CaseRunner(client, RunOptions(sleep=lambda _s: None, **options)), engine


@pytest.mark.parametrize("suite", SUITES)
def test_every_case_runs_through_the_public_api_and_scores(suite):
    runner, engine = _runner()
    _, cases = load_suite(suite)
    records = [runner.run(case) for case in cases]
    for record in records:
        assert record.error is None, (record.case_id, record.error, record.notes)
        assert record.passed, (record.case_id, [(a.label, a.details) for a in record.asks])
    # Every case made and cleaned up its own project.
    assert not engine.projects
    paths = {path for _method, path in engine.calls}
    assert "/v1/projects" in paths and "/v1/knowledge/wiki/write" in paths


def test_handoff_imports_compounds_and_applies_before_asking():
    runner, engine = _runner()
    _, cases = load_suite("cross_agent_handoff")
    record = runner.run(cases[0])
    order = [path for _method, path in engine.calls]
    assert order.index("/v1/conversations/import") < order.index("/v1/compound")
    assert order.index("/v1/compound") < order.index("/v1/knowledge/research")
    # The engine reports a finished compound as "done" (imported_conversations
    # .compound_status); the runner treated only "completed" as compounded and
    # timed out every live handoff case.
    assert record.error is None, record.error
    assert any(note.startswith("compound ") and note.endswith("done") for note in record.notes)


def test_a_stale_answer_fails_the_corrections_case():
    class StaleEngine(FakeEngine):
        def handle(self, request):
            if request.url.path == "/v1/knowledge/research":
                return httpx.Response(200, json={"answer": "The Kd is 38 nM.", "sources": [{}]})
            return super().handle(request)

    runner, _ = _runner(StaleEngine())
    _, cases = load_suite("corrections")
    record = runner.run(next(c for c in cases if c.case_id == "binding-constant"))
    assert record.error is None and not record.passed
    assert record.asks[0].details["forbidden_present"] == ["38 nM"]


def test_compounding_that_never_settles_errors_the_case_and_is_not_scored():
    class StuckEngine(FakeEngine):
        def handle(self, request):
            if request.method == "GET" and request.url.path.startswith("/v1/conversations/"):
                return httpx.Response(200, json={"compound_status": "running"})
            return super().handle(request)

    ticks = iter(range(0, 10_000, 100))
    client, _ = dry_run_client(StuckEngine())
    runner = CaseRunner(
        client,
        RunOptions(sleep=lambda _s: None, clock=lambda: next(ticks), compound_timeout_s=250),
    )
    _, cases = load_suite("cross_agent_handoff")
    record = runner.run(cases[0])
    assert record.error and "did not complete" in record.error
    report = build_report({"cross_agent_handoff": [record]}, run_id="t", target="dry")
    summary = report["summaries"][0]
    assert summary["errored"] == 1 and summary["case_pass_rate"] is None


def test_compounding_report_has_a_slope():
    runner, _ = _runner()
    _, cases = load_suite("compounding")
    report = build_report({"compounding": [runner.run(c) for c in cases]}, run_id="t", target="dry")
    slope = report["summaries"][0]["slope"]
    assert list(slope["pass_rate_by_episode"]) == ["1", "2", "3"]
    assert slope["first_to_last_change"] == 0.0
    assert "Compounding slope" in render_markdown(report)


def test_a_judge_is_recorded_beside_the_deterministic_result():
    seen = []

    def judge(question, answer, expectation):
        seen.append(question)
        return JudgeVerdict(passed=False, rationale="disagrees")

    runner, _ = _runner(judge=judge)
    _, cases = load_suite("corrections")
    record = runner.run(cases[0])
    assert seen and record.asks[0].judge == {"passed": False, "rationale": "disagrees"}
    assert record.asks[0].passed  # the judge never overrides the deterministic score


def test_a_judge_failure_is_not_a_failing_score():
    def judge(question, answer, expectation):
        raise RuntimeError("model unreachable")

    runner, _ = _runner(judge=judge)
    _, cases = load_suite("corrections")
    record = runner.run(cases[0])
    assert record.asks[0].judge["passed"] is None and record.asks[0].passed


@respx.mock
def test_research_asks_send_the_question_scoped_to_the_case_project():
    base = "https://api.bench.test"
    respx.post(f"{base}/v1/projects").respond(201, json={"id": "p-1"})
    respx.delete(f"{base}/v1/projects/p-1").respond(204)
    research = respx.post(f"{base}/v1/knowledge/research").respond(
        json={"answer": "Rafael Ortiz maintains MS-2.", "sources": [{"title": "MS-2"}]}
    )
    client = Beakr(api_key="bk_test_x", base_url=base)
    case = Case(
        suite="cross_agent_handoff",
        case_id="tiny",
        title="tiny",
        steps=(
            {
                "op": "ask",
                "label": "q",
                "question": "Who maintains MS-2?",
                "checks": {"must_include": [["Ortiz"]], "require_citation": True},
            },
        ),
    )
    record = CaseRunner(client, RunOptions(sleep=lambda _s: None)).run(case)
    assert record.passed
    assert json.loads(research.calls.last.request.content) == {
        "query": "Who maintains MS-2?",
        "project_id": "p-1",
    }


def test_cli_dry_run_writes_reports(tmp_path, capsys):
    code = main(["--dry-run", "--suite", "corrections", "--out", str(tmp_path)])
    assert code == 0
    written = sorted(p.suffix for p in tmp_path.glob("beakr-bench-*"))
    assert written == [".json", ".md"]
    assert "| corrections |" in capsys.readouterr().out


def test_cli_refuses_a_live_key(monkeypatch):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_real")
    with pytest.raises(SystemExit, match="Refusing to run with a live key"):
        main(["--suite", "corrections"])


def test_a_seed_whose_write_reports_no_proposal_id_is_still_accepted():
    # The real write door answers a staged write with prose and an outcome
    # sidecar but no proposal id. The runner used to accept nothing, so every
    # seeded page stayed pending and the next step failed with page_not_found.
    runner, engine = _runner()
    _, cases = load_suite("corrections")
    record = runner.run(cases[0])
    assert record.error is None
    accepted = [p for p in engine.proposals.values() if p["status"] == "accepted"]
    assert len(accepted) >= 2  # the seed and its correction
    assert any(path == "/v1/knowledge/wiki/write" for _method, path in engine.calls)
