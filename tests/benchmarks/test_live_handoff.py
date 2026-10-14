"""The live cross-agent handoff harness (EVAL-572), offline.

The harness used to live outside the repo, kept only parsed answers, auto-
accepted proposals without recording it, and deleted each project before any
evidence was saved. These tests drive the real harness against the in-memory
fake engine with stand-in agents, and the real Claude Code / Codex drivers
with recorded streams, to prove the evidence it now keeps.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from beakr_bench.cases import load_suite
from beakr_bench.dryrun import FakeEngine, dry_run_client
from beakr_bench.live_handoff import (
    ACCEPTED_BY,
    CORRECTIONS,
    SUITE,
    HandoffEvidence,
    HandoffRunner,
    _require_explicit_target,
    build_report,
)
from beakr_bench.onboarding.agents import AgentRun, ToolCall
from beakr_bench.onboarding.cli_agents import ClaudeCodeAgent, CodexAgent
from beakr_bench.runner import RunOptions

FIXTURES = Path(__file__).parent / "fixtures"
CASE_ID = "codex-correction"


def _case():
    return next(case for case in load_suite(SUITE)[1] if case.case_id == CASE_ID)


class Producer:
    """Stages an edit through the write door, as Claude Code does with its MCP tools."""

    name = "claude-code"
    model = "stand-in"

    def __init__(self, client):
        self.client = client

    def run(self, *, prompt, context, project_id):
        page = _case().steps[0]["title"]
        self.client.knowledge.write(
            "edit_section",
            scope=project_id,
            page=page,
            sections=[{"title": "Quality", "body": "Minimum base quality is 30."}],
            rationale="correction from the session",
            check=False,
        )
        return AgentRun(
            answer=f"Proposed an edit to {page}.",
            tool_calls=[ToolCall("knowledge_base_write", {"action": "edit_section"})],
            transcript=[{"type": "tool_result", "name": "knowledge_base_write"}],
        )


class Consumer:
    name = "codex"
    model = "stand-in"

    def __init__(self, client, interrupt=False):
        self.client = client
        self.interrupt = interrupt

    def run(self, *, prompt, context, project_id):
        if self.interrupt:
            raise KeyboardInterrupt
        answer = self.client.knowledge.research(prompt, project_id=project_id)
        text = answer["answer"] + " {{wiki:quality}}"
        return AgentRun(
            answer=text,
            tool_calls=[ToolCall("research", {"question": prompt})],
            transcript=[{"type": "tool_result", "name": "research", "result": answer["answer"]}],
        )


def _run(tmp_path, consumer_interrupts=False):
    engine = FakeEngine()
    client, engine = dry_run_client(engine)
    evidence = HandoffEvidence(tmp_path / "evidence")
    runner = HandoffRunner(
        client,
        Producer(client),
        Consumer(client, interrupt=consumer_interrupts),
        evidence,
        RunOptions(sleep=lambda _s: None, run_id="t1"),
        credential_label="bk_test_... (sandbox key)",
    )
    return runner, evidence, engine


def test_every_suite_case_has_a_correction_to_hand_off():
    assert {case.case_id for case in load_suite(SUITE)[1]} == set(CORRECTIONS)


def test_a_case_keeps_its_evidence_and_records_who_accepted(tmp_path):
    runner, evidence, engine = _run(tmp_path)
    record = runner.run(_case())
    assert "error" not in record, record
    assert record["consumer"]["passed"] is True
    acceptance = record["acceptance"]
    assert len(acceptance) == 1 and acceptance[0]["accepted"] is True
    assert acceptance[0]["accepted_by"] == ACCEPTED_BY
    assert acceptance[0]["credential"].startswith("bk_test_")
    assert acceptance[0]["at"] and acceptance[0]["reason"]
    case_dir = evidence.directory / CASE_ID
    proposals = json.loads((case_dir / "proposals.json").read_text())
    assert proposals[0]["body"]["sections"][0]["body"] == "Minimum base quality is 30."
    content = json.loads((case_dir / "accepted_content.json").read_text())
    assert "Minimum base quality is 30." in next(iter(content.values()))
    for role in ("producer", "consumer"):
        payload = json.loads((case_dir / f"{role}.json").read_text())
        assert payload["transcript"] and payload["prompt"]
    stages = [json.loads(line)["stage"] for line in evidence.checkpoint.read_text().splitlines()]
    assert stages == ["seeded", "producer", "accepted", "consumer", "done"]
    assert engine.projects == {}  # deleted only after the evidence was written

    report = build_report(
        [record], run_id="t1", target="dry", evidence_dir=evidence.directory, agents={}
    )
    assert report["consumer_passed"] == 1
    assert report["acceptance_policy"]["accepted_by"] == ACCEPTED_BY


def test_an_interrupted_run_keeps_the_finished_stages(tmp_path):
    runner, evidence, engine = _run(tmp_path, consumer_interrupts=True)
    with pytest.raises(KeyboardInterrupt):
        runner.run(_case())
    stages = [json.loads(line)["stage"] for line in evidence.checkpoint.read_text().splitlines()]
    assert stages == ["seeded", "producer", "accepted", "done"]
    assert (evidence.directory / CASE_ID / "proposals.json").exists()
    assert engine.projects == {}


def _fake_process(stream):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=stream, stderr="")

    return runner


def test_real_drivers_keep_each_clients_raw_event_stream():
    claude_stream = (FIXTURES / "claude_code_stream.jsonl").read_text()
    codex_stream = (FIXTURES / "codex_exec.jsonl").read_text()
    claude = ClaudeCodeAgent(runner=_fake_process(claude_stream)).run(
        prompt="p", context=None, project_id="proj"
    )
    codex = CodexAgent(runner=_fake_process(codex_stream)).run(
        prompt="p", context=None, project_id="proj"
    )
    assert len(claude.transcript) == len([ln for ln in claude_stream.splitlines() if ln.strip()])
    assert claude.transcript[0]["type"] == "system"
    assert len(codex.transcript) == len([ln for ln in codex_stream.splitlines() if ln.strip()])


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "needs BEAKR_API_KEY and BEAKR_API_URL"),
        ({"BEAKR_API_KEY": "bk_test_x"}, "needs BEAKR_API_KEY and BEAKR_API_URL"),
        ({"BEAKR_API_KEY": "bk_live_x", "BEAKR_API_URL": "https://x"}, "Refusing a live key"),
    ],
)
def test_the_run_refuses_an_implicit_or_live_target(monkeypatch, env, message):
    # A run without an explicit sandbox target once let an agent's MCP server
    # fall back to a stored login and read production.
    for name in ("BEAKR_API_KEY", "BEAKR_API_URL", "BEAKR_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match=message):
        _require_explicit_target(allow_live=False)
