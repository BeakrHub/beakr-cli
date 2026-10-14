"""Live-run evidence survives and is reviewable (Codex R016 evidence-retention request).

Root cause: the onboarding CLI kept every run in memory and wrote one report at
the end, so an interrupted hour-long run lost all finished runs, and the report
kept only parsed fields, so a reviewer could not see what an agent did.
"""

from __future__ import annotations

import json

from beakr_bench.dryrun import FakeEngine, dry_run_client
from beakr_bench.onboarding.agents import ApiAgent, ScriptedModel
from beakr_bench.onboarding.cases import load_cases
from beakr_bench.onboarding.cli import main
from beakr_bench.onboarding.cli_agents import raw_events
from beakr_bench.onboarding.evidence import EvidenceLog, load_checkpoint
from beakr_bench.onboarding.harness import OnboardingRunner
from beakr_bench.runner import RunOptions


def _runner(evidence, agent=None):
    client, _engine = dry_run_client(FakeEngine())
    options = RunOptions(sleep=lambda _s: None)
    agent = agent or ApiAgent(client, ScriptedModel(), model="scripted")
    return OnboardingRunner(client, agent, options, evidence=evidence)


def test_each_run_is_written_with_its_transcript_as_it_finishes(tmp_path):
    log = EvidenceLog(tmp_path)
    case = load_cases()[1][0]
    _runner(log).run(case)
    runs = tmp_path / "runs" / case.case_id
    run_files = sorted(runs.glob("*.json"))
    assert [p.name for p in run_files] == ["with_context-r0.json", "without_context-r0.json"]
    without = json.loads((runs / "without_context-r0.json").read_text())
    kinds = [entry["type"] for entry in without["transcript"]]
    assert "model_turn" in kinds and "tool_result" in kinds
    assert without["context"] is None and without["prompt"] == case.prompt
    tool = next(e for e in without["transcript"] if e["type"] == "tool_result")
    assert tool["name"] == "research" and tool["result"]
    with_context = json.loads((runs / "with_context-r0.json").read_text())
    assert with_context["context"]
    assert json.loads((tmp_path / "cases" / f"{case.case_id}.json").read_text())["context"]


def test_an_interrupted_run_keeps_its_finished_runs(tmp_path):
    log = EvidenceLog(tmp_path)
    client, _engine = dry_run_client(FakeEngine())
    inner = ApiAgent(client, ScriptedModel(), model="scripted")

    class Interrupted:
        name = "scripted"
        context_format = inner.context_format
        calls = 0

        def run(self, **kwargs):
            Interrupted.calls += 1
            if Interrupted.calls == 2:
                raise KeyboardInterrupt  # the operator stops the run mid-case
            return inner.run(**kwargs)

    runner = OnboardingRunner(
        client, Interrupted(), RunOptions(sleep=lambda _s: None), evidence=log
    )
    case = load_cases()[1][0]
    try:
        runner.run(case)
    except KeyboardInterrupt:
        pass
    records = load_checkpoint(log.checkpoint)
    assert [r.case_id for r in records] == [case.case_id]
    assert [a.arm for a in records[0].arms] == ["with_context"]


def test_a_report_can_be_built_from_a_checkpoint(tmp_path):
    out = tmp_path / "out"
    assert main(["--dry-run", "--out", str(out), "--evidence-dir", str(tmp_path / "ev")]) == 0
    original = json.loads(next(out.glob("*.json")).read_text())
    assert original["evidence_dir"] == str(tmp_path / "ev")
    again = tmp_path / "again"
    checkpoint = tmp_path / "ev" / "checkpoint.jsonl"
    assert main(["--from-checkpoint", str(checkpoint), "--out", str(again)]) == 0
    rebuilt = json.loads(next(again.glob("*.json")).read_text())
    assert rebuilt["arms"] == original["arms"] and rebuilt["bar"] == original["bar"]
    assert rebuilt["from_checkpoint"] == str(checkpoint)


def test_raw_events_keeps_the_whole_client_stream():
    stdout = '{"type": "a", "x": 1}\nnot json\n\n[1, 2]\n'
    events = raw_events(stdout, "boom")
    assert events == [
        {"type": "a", "x": 1},
        {"type": "_text", "text": "not json"},
        {"type": "_json", "value": [1, 2]},
        {"type": "_stderr", "text": "boom"},
    ]
