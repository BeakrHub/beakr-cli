"""``beakr-bench onboarding``: the agent onboarding eval (with vs without injected context).

    uv run python benchmarks/run_onboarding.py --dry-run
    uv run python benchmarks/run_onboarding.py --agent api --repeats 3 --out results/
    uv run python benchmarks/run_onboarding.py --agent claude-code
    uv run python benchmarks/run_onboarding.py --agent codex

A live run calls a model for every arm of every case (it costs money; see the
README for the estimate) and creates and deletes one project per case, so it
refuses a ``bk_live_`` key unless ``--allow-live`` is passed. ``--dry-run`` runs
the scripted stand-in model against the in-memory fake engine: no network, no
cost, and no claim about any model or about Beakr.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from beakr_bench.cases import DATA_DIR
from beakr_bench.cli import _live_client
from beakr_bench.onboarding import AGENTS, SUITE
from beakr_bench.onboarding.agents import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TURNS,
    DEFAULT_MODEL,
    ApiAgent,
    ScriptedModel,
    anthropic_model,
)
from beakr_bench.onboarding.cases import load_cases
from beakr_bench.onboarding.cli_agents import ClaudeCodeAgent, CodexAgent, require_binary
from beakr_bench.onboarding.evidence import EvidenceLog, load_checkpoint
from beakr_bench.onboarding.harness import OnboardingRunner
from beakr_bench.onboarding.report import build_report, render_markdown, write_report
from beakr_bench.runner import RunOptions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beakr-bench-onboarding", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--agent", choices=AGENTS, default="api")
    parser.add_argument("--model", help=f"Model for the agent (api default {DEFAULT_MODEL}).")
    parser.add_argument("--effort", default=DEFAULT_EFFORT, help="api agent effort level.")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument(
        "--beakr-command",
        default="beakr",
        help="The command Claude Code and Codex start as their Beakr MCP server "
        "(e.g. a wrapper that pins the API, key and an isolated HOME).",
    )
    parser.add_argument(
        "--no-fallbacks", action="store_true", help="api agent: no refusal fallback."
    )
    parser.add_argument("--repeats", type=int, default=1, help="Runs per arm per case.")
    parser.add_argument("--case", action="append", help="Run only these case ids.")
    parser.add_argument(
        "--case-file",
        type=Path,
        help="A case file other than data/agent_onboarding.json (e.g. a pre-registered set); "
        "its SHA-256 is recorded in the report.",
    )
    parser.add_argument("--out", type=Path, default=Path("beakr-bench-results"))
    parser.add_argument("--dry-run", action="store_true", help="Scripted model, fake engine.")
    parser.add_argument("--allow-live", action="store_true", help="Permit a bk_live_ key.")
    parser.add_argument("--keep-projects", action="store_true")
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="Where each run's transcript and the checkpoint go (default: <out>/evidence-<run>).",
    )
    parser.add_argument(
        "--from-checkpoint",
        type=Path,
        help="Build the report from an interrupted run's checkpoint.jsonl; runs nothing.",
    )
    return parser


def _agent(args, client):
    if args.dry_run or args.agent == "scripted":
        return ApiAgent(client, ScriptedModel(), model="scripted", max_turns=args.max_turns)
    if args.agent == "api":
        return ApiAgent(
            client,
            anthropic_model(),
            model=args.model or DEFAULT_MODEL,
            effort=args.effort,
            max_turns=args.max_turns,
            fallbacks=not args.no_fallbacks,
        )
    if args.agent == "claude-code":
        require_binary("claude")
        return ClaudeCodeAgent(
            model=args.model, max_turns=args.max_turns, beakr_command=args.beakr_command
        )
    require_binary("codex")
    return CodexAgent(model=args.model, beakr_command=args.beakr_command)


def _report_from_checkpoint(args) -> int:
    records = load_checkpoint(args.from_checkpoint)
    report = build_report(
        records,
        run_id=f"checkpoint-{args.from_checkpoint.parent.name}",
        target="(from checkpoint)",
        agent={"agent": args.agent, "model": args.model},
    )
    report["from_checkpoint"] = str(args.from_checkpoint)
    json_path, md_path = write_report(report, args.out)
    sys.stdout.write(render_markdown(report))
    sys.stdout.write(f"\nWrote {json_path} and {md_path}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.from_checkpoint:
        return _report_from_checkpoint(args)
    if args.dry_run:
        from beakr_bench.dryrun import DRY_RUN_BASE_URL, dry_run_client

        client, _engine = dry_run_client()
        target = DRY_RUN_BASE_URL
    else:
        client, target = _live_client(args.allow_live)
    agent = _agent(args, client)
    options = RunOptions(keep_projects=args.keep_projects)
    if args.dry_run:
        options.sleep = lambda _seconds: None
    _description, cases = load_cases(path=args.case_file)
    if args.case:
        cases = [case for case in cases if case.case_id in args.case]
    evidence_dir = args.evidence_dir or args.out / f"evidence-{options.run_id}"
    runner = OnboardingRunner(
        client, agent, options, repeats=args.repeats, evidence=EvidenceLog(evidence_dir)
    )
    records = [runner.run(case) for case in cases]
    report = build_report(
        records,
        run_id=options.run_id,
        target=str(target),
        agent={
            "agent": "scripted" if args.dry_run else agent.name,
            "model": getattr(agent, "model", None),
        },
    )
    report["evidence_dir"] = str(evidence_dir)
    case_path = args.case_file or DATA_DIR / f"{SUITE}.json"
    report["case_file"] = {
        "path": str(case_path),
        "sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(),
    }
    json_path, md_path = write_report(report, args.out)
    sys.stdout.write(render_markdown(report))
    sys.stdout.write(f"\nWrote {json_path} and {md_path}; evidence in {evidence_dir}\n")
    return 2 if report["errored"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
