"""The live cross-agent handoff (EVAL-572): one real agent records a correction, another uses it.

    BEAKR_API_KEY=bk_test_... BEAKR_API_URL=https://... \
        uv run python benchmarks/run_live_handoff.py --out results/

For each ``cross_agent_handoff`` case:

1. A fresh sandbox project is seeded with the stale page through the write door
   (the shared ``beakr_bench`` runner).
2. **Producer**: a real Claude Code session is told the correction and asked to
   record it in Beakr with its Beakr MCP tools. It stages proposals.
3. **Acceptance**: a sandbox has no reviewer, so the harness accepts every
   pending proposal itself, with the run's sandbox key. This is recorded
   explicitly per proposal (who, when, with what, and the result), so the
   provenance of what the consumer later reads is reconstructible: it is the
   producer's proposal, accepted by the harness, not by a person.
4. **Consumer**: a real Codex run is asked the suite's question with only its
   Beakr MCP tools, and its answer is scored with the suite's own checks.

The project is deleted afterwards, so everything needed to review a case is
kept first, under ``--evidence-dir``, as each stage ends:

- ``<case>/producer.json`` and ``<case>/consumer.json``: prompt, answer, tool
  calls and the client's raw event stream;
- ``<case>/proposals.json``: each staged proposal's full body as the API
  returned it, and the acceptance record;
- ``<case>/accepted_content.json``: the pages the proposals touched, read
  back after acceptance (what the consumer could find);
- ``checkpoint.jsonl``: one line per finished stage, flushed as it ends, so an
  interrupted run keeps every finished case and stage.

The run refuses to start without ``BEAKR_API_KEY`` and ``BEAKR_API_URL`` in its
environment: both agents' Beakr MCP servers are given exactly those, so neither
can fall back to a person's stored login (which once sent a run's Codex
lookups to production). A ``bk_live_`` key is refused unless ``--allow-live``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from beakr_bench.cases import Case, load_suite
from beakr_bench.extract import answer_text
from beakr_bench.runner import CaseRecord, CaseRunner, RunOptions
from beakr_bench.scoring import SCORER_VERSION, score_answer

SUITE = "cross_agent_handoff"

#: What the producer is told, per case. Every suite case needs one.
CORRECTIONS = {
    "claude-code-correction": (
        "Since the 2026-05-12 protocol revision lysis buffer LB-3 uses 300 mM NaCl, not "
        "150 mM; the 150 mM version lysed nuclei too slowly. Triton X-100 stays at 1 percent."
    ),
    "codex-correction": (
        "We raised the variant calling pipeline minimum base quality to 30 in the 2026-06-02 "
        "release because quality 20 let through too many false positives in low-complexity "
        "regions."
    ),
    "claude-code-new-owner": (
        "Dana Whitfield handed mass spectrometer MS-2 maintenance to Rafael Ortiz on "
        "2026-07-14, so Rafael Ortiz maintains MS-2 now."
    ),
}

PRODUCER_PROMPT = (
    "{correction}\n\nRecord this correction in our team knowledge in Beakr: find the page "
    "that holds the old value and propose an edit that replaces it, citing this "
    "conversation. Then say what you proposed."
)

ACCEPTED_BY = "beakr-bench live handoff harness"
ACCEPTANCE_REASON = (
    "A sandbox org has no reviewer; the harness accepts the producer's proposals so the "
    "consumer can read them. Not a human review."
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name) or "_"


class HandoffEvidence:
    """Per-case evidence files and an append-only checkpoint, written as stages end."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.checkpoint = self.directory / "checkpoint.jsonl"

    def write(self, case_id: str, name: str, payload: Any) -> Path:
        path = self.directory / _safe(case_id) / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str))
        return path

    def stage(self, case_id: str, stage: str, summary: dict[str, Any]) -> None:
        line = {"case_id": case_id, "stage": stage, "at": _now(), **summary}
        with self.checkpoint.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _agent_payload(role: str, agent: Any, prompt: str, run: Any) -> dict[str, Any]:
    return {
        "role": role,
        "agent": getattr(agent, "name", type(agent).__name__),
        "model": getattr(agent, "model", None),
        "prompt": prompt,
        "answer": run.answer,
        "error": run.error,
        "knowledge_lookups": run.knowledge_lookups,
        "tool_calls": [asdict(call) for call in run.tool_calls],
        "turns": run.turns,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "cost_usd": run.cost_usd,
        "wall_s": run.wall_s,
        "transcript": run.transcript,
    }


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in {"transcript", "prompt"}}


def _proposal_id(proposal: dict[str, Any]) -> str:
    return str(proposal.get("proposal_id") or proposal.get("id"))


def _page_title(detail: Any) -> str | None:
    if not isinstance(detail, dict):
        return None
    for key in ("page_title", "title", "page"):
        if detail.get(key):
            return str(detail[key])
    return None


class HandoffRunner:
    def __init__(
        self,
        client: Any,
        producer: Any,
        consumer: Any,
        evidence: HandoffEvidence,
        options: RunOptions,
        *,
        credential_label: str,
    ) -> None:
        self.client = client
        self.producer = producer
        self.consumer = consumer
        self.evidence = evidence
        self.options = options
        self.credential_label = credential_label
        self._seeder = CaseRunner(client, options)

    def _accept_all(self, case_id: str, project_id: str) -> list[dict[str, Any]]:
        listed = self.client.knowledge.proposals.list(project_id=project_id) or []
        proposals = []
        for proposal in listed:
            pid = _proposal_id(proposal)
            try:
                detail = self.client.knowledge.proposals.show(pid)
            except Exception as exc:  # kept as evidence, not fatal
                detail = {"error": f"{type(exc).__name__}: {exc}", "listed": proposal}
            acceptance: dict[str, Any] = {
                "accepted_by": ACCEPTED_BY,
                "credential": self.credential_label,
                "reason": ACCEPTANCE_REASON,
                "at": _now(),
            }
            try:
                acceptance["result"] = self.client.knowledge.proposals.accept(pid)
                acceptance["accepted"] = True
            except Exception as exc:
                acceptance["accepted"] = False
                acceptance["error"] = f"{type(exc).__name__}: {exc}"
            proposals.append({"proposal_id": pid, "body": detail, "acceptance": acceptance})
        return proposals

    def _accepted_content(self, project_id: str, proposals: list[dict]) -> dict[str, str]:
        content: dict[str, str] = {}
        for proposal in proposals:
            title = _page_title(proposal["body"])
            if not title or title in content or not proposal["acceptance"].get("accepted"):
                continue
            try:
                response = self.client.knowledge.relay_command(
                    "cat", {"page": title}, scope=project_id, check=False
                )
                content[title] = answer_text(response)
            except Exception as exc:
                content[title] = f"(could not read back: {type(exc).__name__}: {exc})"
        return content

    def run(self, case: Case) -> dict[str, Any]:
        record: dict[str, Any] = {"case_id": case.case_id, "title": case.title}
        project_id: str | None = None
        try:
            correction = CORRECTIONS[case.case_id]
            project = self.client.projects.create(
                f"beakr-bench live handoff {case.case_id} {self.options.run_id}",
                description="Created by beakr-bench; safe to delete.",
            )
            project_id = record["project_id"] = str(project["id"])
            seed = CaseRecord(suite=case.suite, case_id=case.case_id, title=case.title)
            seed.project_id = project_id
            for index, step in enumerate(case.steps):
                if step["op"] == "seed_page":
                    self._seeder.apply_step(case, index, step, seed)
            record["seed_notes"] = list(seed.notes)
            self.evidence.stage(case.case_id, "seeded", {"project_id": project_id})

            prompt = PRODUCER_PROMPT.format(correction=correction)
            produced = self.producer.run(prompt=prompt, context=None, project_id=project_id)
            payload = _agent_payload("producer", self.producer, prompt, produced)
            record["producer"] = _summary(payload)
            path = self.evidence.write(case.case_id, "producer", payload)
            record["producer"]["evidence"] = str(path)
            self.evidence.stage(case.case_id, "producer", {"error": produced.error})

            proposals = self._accept_all(case.case_id, project_id)
            accepted_content = self._accepted_content(project_id, proposals)
            self.evidence.write(case.case_id, "proposals", proposals)
            self.evidence.write(case.case_id, "accepted_content", accepted_content)
            record["acceptance"] = [
                {
                    "proposal_id": p["proposal_id"],
                    "page": _page_title(p["body"]),
                    **{k: v for k, v in p["acceptance"].items() if k != "result"},
                }
                for p in proposals
            ]
            self.evidence.stage(
                case.case_id,
                "accepted",
                {"proposals": len(proposals), "pages_read_back": sorted(accepted_content)},
            )

            ask = next(step for step in case.steps if step["op"] == "ask")
            consumed = self.consumer.run(
                prompt=ask["question"], context=None, project_id=project_id
            )
            payload = _agent_payload("consumer", self.consumer, ask["question"], consumed)
            score = score_answer(consumed.answer, [], ask["checks"])
            record["consumer"] = {
                **_summary(payload),
                "passed": score.passed and consumed.error is None,
                "checks": score.checks,
                "details": score.details,
                "evidence": str(self.evidence.write(case.case_id, "consumer", payload)),
            }
            self.evidence.stage(case.case_id, "consumer", {"passed": record["consumer"]["passed"]})
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if project_id and not self.options.keep_projects:
                try:
                    self.client.projects.delete(project_id)
                except Exception as exc:
                    record.setdefault("notes", []).append(f"cleanup: {type(exc).__name__}")
            self.evidence.stage(case.case_id, "done", {"error": record.get("error")})
        return record


def build_report(
    records: list[dict[str, Any]], *, run_id: str, target: str, evidence_dir: Path, agents: dict
) -> dict[str, Any]:
    scored = [r for r in records if "error" not in r]
    passed = sum(1 for r in scored if (r.get("consumer") or {}).get("passed"))
    return {
        "benchmark": "beakr-bench",
        "suite": "live_cross_agent_handoff",
        "run_id": run_id,
        "target": target,
        "generated_at": _now(),
        "scorer_version": SCORER_VERSION,
        "agents": agents,
        "acceptance_policy": {"accepted_by": ACCEPTED_BY, "reason": ACCEPTANCE_REASON},
        "evidence_dir": str(evidence_dir),
        "cases": len(records),
        "errored": len(records) - len(scored),
        "consumer_passed": passed,
        "consumer_pass_rate": round(passed / len(scored), 4) if scored else None,
        "records": records,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beakr-bench-live-handoff", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--out", type=Path, default=Path("beakr-bench-results"))
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--case", action="append", help="Run only these case ids.")
    parser.add_argument("--claude-model", help="Producer model (Claude Code's default if unset).")
    parser.add_argument("--codex-model", help="Consumer model (Codex's default if unset).")
    parser.add_argument("--timeout", type=float, default=900.0, help="Seconds per agent run.")
    parser.add_argument("--allow-live", action="store_true", help="Permit a bk_live_ key.")
    parser.add_argument("--keep-projects", action="store_true")
    parser.add_argument(
        "--beakr-command",
        default="beakr",
        help="The command both agents start as their Beakr MCP server.",
    )
    return parser


def _require_explicit_target(allow_live: bool) -> str:
    key = os.environ.get("BEAKR_API_KEY", "")
    url = os.environ.get("BEAKR_API_URL") or os.environ.get("BEAKR_BASE_URL") or ""
    if not key or not url:
        raise SystemExit(
            "The live handoff needs BEAKR_API_KEY and BEAKR_API_URL set for a sandbox org: "
            "both agents' Beakr MCP servers get exactly these, so neither can use a stored login."
        )
    if key.startswith("bk_live_") and not allow_live:
        raise SystemExit("Refusing a live key; use a sandbox org's bk_test_ key or --allow-live.")
    os.environ.setdefault("BEAKR_BASE_URL", url)
    os.environ.setdefault("BEAKR_API_URL", url)
    return f"{key[:8]}... ({'live' if key.startswith('bk_live_') else 'sandbox'} key)"


def main(argv: list[str] | None = None, *, producer: Any = None, consumer: Any = None) -> int:
    from beakr_sdk import Beakr

    from beakr_bench.onboarding.cli_agents import ClaudeCodeAgent, CodexAgent, require_binary

    args = _parser().parse_args(argv)
    credential = _require_explicit_target(args.allow_live)
    if producer is None:
        require_binary("claude")
        producer = ClaudeCodeAgent(
            model=args.claude_model, timeout_s=args.timeout, beakr_command=args.beakr_command
        )
    if consumer is None:
        require_binary("codex")
        consumer = CodexAgent(
            model=args.codex_model, timeout_s=args.timeout, beakr_command=args.beakr_command
        )
    client = Beakr()
    options = RunOptions(keep_projects=args.keep_projects)
    evidence_dir = args.evidence_dir or args.out / f"handoff-evidence-{options.run_id}"
    evidence = HandoffEvidence(evidence_dir)
    runner = HandoffRunner(
        client, producer, consumer, evidence, options, credential_label=credential
    )
    _description, cases = load_suite(SUITE)
    if args.case:
        cases = [case for case in cases if case.case_id in args.case]
    records = []
    for case in cases:
        records.append(runner.run(case))
        sys.stdout.write(json.dumps({k: records[-1].get(k) for k in ("case_id", "error")}) + "\n")
    report = build_report(
        records,
        run_id=options.run_id,
        target=str(client.base_url),
        evidence_dir=evidence_dir,
        agents={
            "producer": {"agent": producer.name, "model": getattr(producer, "model", None)},
            "consumer": {"agent": consumer.name, "model": getattr(consumer, "model", None)},
        },
    )
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"live-handoff-{options.run_id}-{time.strftime('%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    sys.stdout.write(
        f"live handoff: {report['consumer_passed']}/{len(records) - report['errored']} consumer "
        f"answers used the correction; wrote {path}; evidence in {evidence_dir}\n"
    )
    return 2 if report["errored"] else 0
