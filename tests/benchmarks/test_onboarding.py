"""The agent onboarding eval (EVAL-571): harness, agent loop, CLI-agent drivers and scoring.

Everything runs offline: the scripted model behind the real tool-use loop, the
in-memory fake engine behind the real SDK, and recorded Claude Code / Codex
event streams behind the real parsers. None of it says anything about a model
or about Beakr; it proves the eval measures what it says it measures.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from beakr_bench.cases import CaseFileError
from beakr_bench.dryrun import FakeEngine, dry_run_client
from beakr_bench.onboarding import ARMS
from beakr_bench.onboarding.agents import (
    CONTEXT_HEADER,
    FALLBACK_BETA,
    AgentRun,
    ApiAgent,
    ScriptedModel,
    estimate_cost,
)
from beakr_bench.onboarding.cases import load_cases
from beakr_bench.onboarding.cli import main
from beakr_bench.onboarding.cli_agents import (
    ClaudeCodeAgent,
    CodexAgent,
    parse_claude_stream,
    parse_codex_events,
)
from beakr_bench.onboarding.harness import OnboardingRunner, score_run
from beakr_bench.onboarding.report import bar, build_report, render_markdown
from beakr_bench.runner import RunOptions

FIXTURES = Path(__file__).parent / "fixtures"


def _runner(agent_factory=None, repeats: int = 1):
    client, engine = dry_run_client(FakeEngine())
    agent = (agent_factory or (lambda c: ApiAgent(c, ScriptedModel(), model="scripted")))(client)
    return OnboardingRunner(
        client, agent, RunOptions(sleep=lambda _s: None), repeats=repeats
    ), engine


# -- cases ---------------------------------------------------------------------


def test_every_case_loads_with_setup_and_checks():
    _description, cases = load_cases()
    assert len(cases) >= 5
    for case in cases:
        assert case.setup and case.prompt and case.checks


def test_a_case_without_setup_is_refused(tmp_path):
    (tmp_path / "agent_onboarding.json").write_text(
        json.dumps(
            {
                "suite": "agent_onboarding",
                "cases": [{"id": "x", "setup": [], "task": {"prompt": "p", "checks": {}}}],
            }
        )
    )
    with pytest.raises(CaseFileError, match="setup"):
        load_cases(data_dir=tmp_path)


def test_an_ask_step_is_not_valid_setup(tmp_path):
    (tmp_path / "agent_onboarding.json").write_text(
        json.dumps(
            {
                "suite": "agent_onboarding",
                "cases": [
                    {
                        "id": "x",
                        "setup": [{"op": "ask", "label": "a", "question": "q", "checks": {}}],
                        "task": {"prompt": "p", "checks": {"must_include": [["a"]]}},
                    }
                ],
            }
        )
    )
    with pytest.raises(CaseFileError, match="op must be"):
        load_cases(data_dir=tmp_path)


# -- harness end to end --------------------------------------------------------


def test_each_case_runs_both_arms_through_the_public_api_and_cleans_up():
    runner, engine = _runner(repeats=2)
    _description, cases = load_cases()
    records = [runner.run(case) for case in cases]
    for record in records:
        assert record.error is None, (record.case_id, record.error)
        assert record.context_chars > 0
        assert sorted({a.arm for a in record.arms}) == sorted(ARMS)
        assert len(record.arms) == 2 * len(ARMS)
    assert not engine.projects
    paths = [path for _method, path in engine.calls]
    assert "/v1/knowledge/wiki/write" in paths


def test_context_is_fetched_once_before_either_arm_runs():
    # The without-context arm's lookups must not be able to change what the
    # with-context arm is given (a session bookmark, recent activity).
    runner, engine = _runner()
    _description, cases = load_cases()
    runner.run(cases[0])
    paths = [path for _method, path in engine.calls]
    assert paths.count("/v1/context") == 1
    assert paths.index("/v1/context") < paths.index("/v1/knowledge/research")


def test_the_scripted_agent_looks_up_only_without_context():
    runner, _engine = _runner()
    _description, cases = load_cases()
    record = runner.run(cases[0])
    by_arm = {a.arm: a for a in record.arms}
    assert by_arm["with_context"].knowledge_lookups == 0
    assert by_arm["without_context"].knowledge_lookups == 1
    assert by_arm["without_context"].tools[0]["name"] == "research"


def test_seeding_failure_is_an_errored_case_not_a_failed_task():
    class Broken(FakeEngine):
        def handle(self, request):
            if request.url.path == "/v1/knowledge/wiki/write":
                import httpx

                return httpx.Response(500, json={"error": {"code": "internal_error"}})
            return super().handle(request)

    client, engine = dry_run_client(Broken())
    runner = OnboardingRunner(
        client,
        ApiAgent(client, ScriptedModel(), model="scripted"),
        RunOptions(sleep=lambda _s: None),
    )
    _description, cases = load_cases()
    record = runner.run(cases[0])
    assert record.error and not record.arms
    assert not engine.projects  # still cleaned up
    report = build_report([record], run_id="r", target="t", agent={"agent": "scripted"})
    assert report["errored"] == 1 and report["arms"]["with_context"]["runs"] == 0


# -- the API agent loop --------------------------------------------------------


def _response(content, stop, model="claude-opus-5", tokens=(100, 10, 50)):
    return SimpleNamespace(
        model=model,
        content=content,
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=tokens[0], output_tokens=tokens[1], cache_read_input_tokens=tokens[2]
        ),
    )


class _Recorder:
    """A Messages-API stand-in that plays back a script and records requests."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = SimpleNamespace(create=lambda **kw: self._next("plain", kw))
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: self._next("beta", kw))
        )

    def _next(self, surface, kwargs):
        self.calls.append((surface, json.loads(json.dumps(kwargs, default=lambda o: vars(o)))))
        return self.script.pop(0)


def _two_tool_turn():
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    uses = [
        {"type": "tool_use", "id": "a", "name": "research", "input": {"question": "q1"}},
        {"type": "tool_use", "id": "b", "name": "knowledge_base", "input": {"command": "grep"}},
    ]
    return [
        _response([thinking, *uses], "tool_use"),
        _response([{"type": "text", "text": "done"}], "end_turn"),
    ]


def test_parallel_tool_results_return_in_one_message_and_thinking_is_replayed():
    client, _engine = dry_run_client()
    model = _Recorder(_two_tool_turn())
    run = ApiAgent(client, model, model="claude-sonnet-5").run(
        prompt="task", context=None, project_id="p"
    )
    assert run.answer == "done" and run.error is None
    assert run.knowledge_lookups == 2 and run.turns == 2
    second = model.calls[1][1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["content"][0]["type"] == "thinking"
    results = second[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert all(r["type"] == "tool_result" for r in results)
    assert run.input_tokens == 200 and run.cache_read_tokens == 100 and run.output_tokens == 20


def test_opus_declares_the_documented_refusal_fallback_and_records_the_served_model():
    client, _engine = dry_run_client()
    script = [_response([{"type": "text", "text": "ok"}], "end_turn", model="claude-opus-4-8")]
    model = _Recorder(script)
    run = ApiAgent(client, model, model="claude-opus-5").run(
        prompt="t", context=None, project_id="p"
    )
    surface, kwargs = model.calls[0]
    assert surface == "beta" and kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["fallbacks"] == [{"model": "claude-opus-4-8"}]
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert run.models_served == ["claude-opus-4-8"]


def test_no_fallbacks_uses_the_plain_messages_endpoint():
    client, _engine = dry_run_client()
    model = _Recorder([_response([{"type": "text", "text": "ok"}], "end_turn")])
    ApiAgent(client, model, model="claude-opus-5", fallbacks=False).run(
        prompt="t", context=None, project_id="p"
    )
    assert model.calls[0][0] == "plain"


def test_context_goes_into_the_system_prompt_only_in_the_with_arm():
    client, _engine = dry_run_client()
    model = _Recorder([_response([{"type": "text", "text": "a"}], "end_turn")] * 2)
    agent = ApiAgent(client, model, model="claude-sonnet-5")
    agent.run(prompt="t", context="TEAM FACT", project_id="p")
    agent.run(prompt="t", context=None, project_id="p")
    assert CONTEXT_HEADER in model.calls[0][1]["system"]
    assert "TEAM FACT" in model.calls[0][1]["system"]
    assert CONTEXT_HEADER not in model.calls[1][1]["system"]


@pytest.mark.parametrize(("stop", "error"), [("refusal", "refusal"), ("max_tokens", "max_tokens")])
def test_a_refusal_or_truncation_is_an_agent_error(stop, error):
    client, _engine = dry_run_client()
    model = _Recorder([_response([{"type": "text", "text": "partial"}], stop)])
    run = ApiAgent(client, model, model="claude-sonnet-5").run(
        prompt="t", context=None, project_id="p"
    )
    assert run.error == error
    scored = score_run("with_context", 0, run, {"must_include": [["partial"]]})
    assert scored.passed is False


def test_running_out_of_turns_is_an_agent_error():
    client, _engine = dry_run_client()
    loop = [
        _response(
            [{"type": "tool_use", "id": "x", "name": "research", "input": {"question": "q"}}],
            "tool_use",
        )
    ]
    model = _Recorder(loop * 3)
    run = ApiAgent(client, model, model="claude-sonnet-5", max_turns=3).run(
        prompt="t", context=None, project_id="p"
    )
    assert run.error == "no answer within 3 turns" and run.knowledge_lookups == 3


def test_cost_uses_published_prices_and_discounts_cache_reads():
    run = AgentRun(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert estimate_cost("claude-opus-5", run) == pytest.approx(5.0 + 25.0 + 0.5)
    assert estimate_cost("scripted", run) is None


# -- Claude Code and Codex drivers ---------------------------------------------


def test_claude_stream_counts_only_beakr_tool_calls():
    run = parse_claude_stream((FIXTURES / "claude_code_stream.jsonl").read_text())
    assert [c.name for c in run.tool_calls] == ["research"]
    assert run.answer.startswith("buffer: PBS-T")
    assert run.turns == 3 and run.cost_usd == pytest.approx(0.0412)
    assert run.input_tokens == 2700 and run.cache_read_tokens == 14000 and run.output_tokens == 180
    assert run.error is None


def test_codex_events_count_only_beakr_mcp_calls():
    run = parse_codex_events((FIXTURES / "codex_exec.jsonl").read_text())
    assert [c.name for c in run.tool_calls] == ["research"]
    assert run.answer.endswith("volume_ul: 300")
    assert run.turns == 1 and run.input_tokens == 5200 and run.cache_read_tokens == 3100
    assert run.error is None


def test_a_stream_without_a_result_is_an_agent_error():
    assert parse_claude_stream('{"type":"system"}\nnot json\n').error
    assert parse_codex_events('{"type":"turn.failed","error":{"message":"boom"}}').error == "boom"


def _fake_process(stdout, seen):
    def run(argv, **kwargs):
        seen.append((argv, kwargs, _agents_md(kwargs["cwd"])))
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return run


def _agents_md(cwd):
    path = Path(cwd, "AGENTS.md")
    return path.read_text() if path.exists() else None


def test_claude_code_gets_context_through_the_system_prompt_beside_a_shared_preamble():
    from beakr_bench.onboarding.agents import SYSTEM_PREAMBLE

    seen = []
    stream = (FIXTURES / "claude_code_stream.jsonl").read_text()
    agent = ClaudeCodeAgent(runner=_fake_process(stream, seen))
    with_run = agent.run(prompt="task", context="TEAM FACT", project_id="proj-1")
    agent.run(prompt="task", context=None, project_id="proj-1")
    (with_argv, with_kwargs, _), (without_argv, _, _) = seen
    assert "--append-system-prompt" in with_argv
    assert "TEAM FACT" in with_argv[with_argv.index("--append-system-prompt") + 1]
    # Both arms get the same preamble; only the with-context arm adds the fact.
    without_prompt = without_argv[without_argv.index("--append-system-prompt") + 1]
    assert without_prompt.strip() == SYSTEM_PREAMBLE.strip()
    assert "TEAM FACT" not in without_prompt
    for argv in (with_argv, without_argv):
        assert "--strict-mcp-config" in argv
        assert json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["beakr"]["args"] == [
            "mcp"
        ]
        assert "proj-1" in argv[2]
    assert with_kwargs["env"]["BEAKR_PROJECT_ID"] == "proj-1"
    assert with_run.knowledge_lookups == 1


def test_codex_reads_beakr_context_from_agents_md_exactly_as_the_hook_writes_it():
    from beakr_cli.agent_hooks import render_agents_block

    seen = []
    events = (FIXTURES / "codex_exec.jsonl").read_text()
    agent = CodexAgent(runner=_fake_process(events, seen))
    agent.run(prompt="task", context="TEAM FACT", project_id="proj-1")
    agent.run(prompt="task", context=None, project_id="proj-1")
    (with_argv, _, with_md), (_without_argv, _, without_md) = seen
    from beakr_bench.onboarding.agents import SYSTEM_PREAMBLE

    assert with_md == SYSTEM_PREAMBLE + "\n\n" + render_agents_block("TEAM FACT") + "\n"
    assert without_md == SYSTEM_PREAMBLE + "\n"
    assert 'mcp_servers.beakr.args=["mcp"]' in with_argv
    # Headless Codex refuses MCP calls unless the server's tools are approved.
    assert 'mcp_servers.beakr.default_tools_approval_mode="approve"' in with_argv
    # Codex scrubs the MCP server's environment; without forwarding, `beakr mcp`
    # used the stored login and read the person's production org, not the sandbox.
    forwarded = next(a for a in with_argv if a.startswith("mcp_servers.beakr.env_vars="))
    for name in ("BEAKR_API_KEY", "BEAKR_API_URL", "BEAKR_PROJECT_ID"):
        assert name in forwarded
    assert not any("bk_" in a for a in with_argv), "credentials must not reach argv"


def test_a_missing_binary_is_an_agent_error_not_a_crash():
    def missing(argv, **kwargs):
        raise FileNotFoundError("claude")

    run = ClaudeCodeAgent(runner=missing).run(prompt="t", context=None, project_id="p")
    assert run.error and "FileNotFoundError" in run.error


# -- report and bar ------------------------------------------------------------


def _summary(success, lookups, runs=4):
    return {"runs": runs, "success_rate": success, "mean_knowledge_lookups": lookups}


def test_the_bar_needs_both_higher_success_and_fewer_lookups():
    assert bar({"with_context": _summary(0.9, 0.5), "without_context": _summary(0.6, 2.0)})["met"]
    assert not bar({"with_context": _summary(0.9, 2.0), "without_context": _summary(0.6, 2.0)})[
        "met"
    ]
    assert not bar({"with_context": _summary(0.6, 0.5), "without_context": _summary(0.6, 2.0)})[
        "met"
    ]


def test_the_bar_reports_a_success_ceiling_instead_of_hiding_it():
    verdict = bar({"with_context": _summary(1.0, 0.0), "without_context": _summary(1.0, 1.0)})
    assert verdict["success_at_ceiling"] and verdict["met"] is False


def test_dry_run_cli_writes_json_and_markdown(tmp_path, capsys):
    assert main(["--dry-run", "--out", str(tmp_path), "--repeats", "2"]) == 0
    reports = list(tmp_path.glob("beakr-bench-agent_onboarding-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["arms"]["with_context"]["runs"] == report["arms"]["without_context"]["runs"]
    assert report["arms"]["with_context"]["mean_knowledge_lookups"] == 0
    assert "fewer_lookups_with_context" in report["bar"]
    assert "Bar: higher task success" in capsys.readouterr().out
    assert render_markdown(report).startswith("# beakr-bench: agent onboarding")


def test_the_api_agent_drives_the_real_anthropic_sdk_offline():
    """The loop with the real ``anthropic`` client over a mock transport (no network, no cost)."""
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    replies = [
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "research",
                    "input": {"question": "q"},
                }
            ],
            "stop_reason": "tool_use",
        },
        {"content": [{"type": "text", "text": "PBS-T, four washes"}], "stop_reason": "end_turn"},
    ]
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        reply = replies[len(bodies) - 1]
        return httpx2.Response(
            200,
            json={
                "id": f"msg_{len(bodies)}",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "stop_sequence": None,
                "usage": {"input_tokens": 50, "output_tokens": 5, "cache_read_input_tokens": 0},
                **reply,
            },
        )

    model = anthropic.Anthropic(
        api_key="test",
        base_url="https://mock.anthropic",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    client, _engine = dry_run_client()
    run = ApiAgent(client, model, model="claude-opus-5").run(
        prompt="task", context=None, project_id="p"
    )
    assert run.error is None and run.answer == "PBS-T, four washes"
    assert run.knowledge_lookups == 1 and run.turns == 2
    assert bodies[0]["fallbacks"] == [{"model": "claude-opus-4-8"}]
    second = bodies[1]["messages"]
    assert second[1]["content"][0]["type"] == "tool_use"
    assert second[2]["content"][0]["type"] == "tool_result"
    assert run.cost_usd == pytest.approx((100 * 5 + 10 * 25) / 1_000_000)


def test_codex_arms_share_the_same_preamble_and_differ_only_by_context(monkeypatch):
    # C058: the no-context arm must get the same instructions, or a better
    # result would measure the instructions rather than the context.
    import subprocess

    from beakr_bench.onboarding.agents import SYSTEM_PREAMBLE
    from beakr_bench.onboarding.cli_agents import CodexAgent

    written: dict[str, str] = {}

    def runner(argv, cwd, **_):
        written[cwd] = (Path(cwd) / "AGENTS.md").read_text()
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    agent = CodexAgent(runner=runner)
    agent.run(prompt="Describe our units", context=None, project_id="p1")
    agent.run(prompt="Describe our units", context="The lab uses metric units.", project_id="p1")
    without, with_context = written.values()
    assert without.strip() == SYSTEM_PREAMBLE.strip()
    assert with_context.startswith(SYSTEM_PREAMBLE)
    assert "metric units" in with_context and "metric units" not in without


def test_codex_warning_events_do_not_fail_a_completed_run():
    # Codex emits warnings ("Model metadata ... not found", "Skill descriptions
    # were shortened") as top-level "error" events and then completes the turn.
    # The parser treated any "error" event as a failed run.
    from beakr_bench.onboarding.cli_agents import parse_codex_events

    stream = "\n".join(
        json.dumps(e)
        for e in [
            {"type": "error", "message": "Skill descriptions were shortened to fit."},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        ]
    )
    run = parse_codex_events(stream)
    assert run.error is None
    assert run.answer == "OK"
    failed = parse_codex_events(json.dumps({"type": "error", "message": "model not supported"}))
    assert failed.error == "model not supported"


def test_a_timed_out_agent_leaves_no_process_behind(tmp_path):
    # A hung `codex exec` used to leave its native binary running after the
    # timeout (subprocess.run kills only the Node launcher), one orphan per run.
    import os
    import subprocess
    import time

    import pytest
    from beakr_bench.onboarding.cli_agents import run_in_process_group

    pid_file = tmp_path / "child.pid"
    script = f"sleep 60 & echo $! > {pid_file}; wait"
    with pytest.raises(subprocess.TimeoutExpired):
        run_in_process_group(["sh", "-c", script], timeout=1, text=True)
    child = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_the_group_runner_returns_output_like_subprocess_run():
    from beakr_bench.onboarding.cli_agents import run_in_process_group

    done = run_in_process_group(["sh", "-c", "echo hi; echo err >&2"], text=True, timeout=10)
    assert (done.returncode, done.stdout, done.stderr) == (0, "hi\n", "err\n")


def test_an_agent_never_waits_on_the_harness_stdin():
    # `codex exec` reads stdin to EOF when stdin is not a terminal. Run from a
    # background job with an open stdin, every Codex run hung until its timeout.
    import os
    import subprocess

    from beakr_bench.onboarding.cli_agents import run_in_process_group

    read_end, write_end = os.pipe()  # an open stdin that never reaches EOF
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        done = run_in_process_group(["sh", "-c", "cat; echo done"], text=True, timeout=5)
    finally:
        os.dup2(saved, 0)
        for fd in (read_end, write_end, saved):
            os.close(fd)
    assert done.stdout == "done\n"
    assert done.returncode == 0
    assert subprocess.DEVNULL is not None
