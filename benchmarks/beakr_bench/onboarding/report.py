"""Onboarding results as JSON and Markdown, and the spec's bar.

Per arm: task success rate, mean Knowledge lookups, mean turns, mean tokens,
mean wall time and total estimated cost. The bar is the spec's: **higher task
success and fewer Knowledge lookups with context**. Both comparisons are
strict; when both arms already succeed on every task, success cannot be
higher, and the report says so (``success_at_ceiling``) instead of hiding it.
Errored cases (the sandbox failed, seeding failed) are listed and excluded;
an agent that fails to answer is a failed task, not an errored case.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from beakr_bench.onboarding import ARMS, SUITE
from beakr_bench.onboarding.harness import ArmResult, OnboardingRecord


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def arm_summary(results: list[ArmResult]) -> dict[str, Any]:
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    return {
        "runs": len(results),
        "success_rate": _avg([1.0 if r.passed else 0.0 for r in results]),
        "mean_knowledge_lookups": _avg([float(r.knowledge_lookups) for r in results]),
        "mean_turns": _avg([float(r.turns) for r in results]),
        "mean_input_tokens": _avg([float(r.input_tokens + r.cache_read_tokens) for r in results]),
        "mean_output_tokens": _avg([float(r.output_tokens) for r in results]),
        "mean_wall_s": _avg([r.wall_s for r in results]),
        "agent_errors": sum(1 for r in results if r.error),
        "total_cost_usd": round(sum(costs), 4) if costs else None,
    }


def bar(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    with_, without = arms["with_context"], arms["without_context"]
    if not with_["runs"] or not without["runs"]:
        return {"met": None, "reason": "no scored runs"}
    higher = with_["success_rate"] > without["success_rate"]
    fewer = with_["mean_knowledge_lookups"] < without["mean_knowledge_lookups"]
    return {
        "higher_success_with_context": higher,
        "fewer_lookups_with_context": fewer,
        "success_at_ceiling": with_["success_rate"] == without["success_rate"] == 1.0,
        "success_delta": round(with_["success_rate"] - without["success_rate"], 4),
        "lookup_delta": round(
            with_["mean_knowledge_lookups"] - without["mean_knowledge_lookups"], 4
        ),
        "met": higher and fewer,
    }


def build_report(
    records: list[OnboardingRecord], *, run_id: str, target: str, agent: dict[str, Any]
) -> dict[str, Any]:
    scored = [r for r in records if r.error is None]
    arms = {arm: arm_summary([a for r in scored for a in r.arms if a.arm == arm]) for arm in ARMS}
    return {
        "benchmark": "beakr-bench",
        "suite": SUITE,
        "run_id": run_id,
        "target": target,
        "agent": agent,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cases": len(records),
        "errored": len(records) - len(scored),
        "arms": arms,
        "bar": bar(arms),
        "records": [asdict(record) for record in records],
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}"


def render_markdown(report: dict[str, Any]) -> str:
    agent = report["agent"]
    lines = [
        "# beakr-bench: agent onboarding",
        "",
        f"Run `{report['run_id']}` against `{report['target']}`, {report['generated_at']}. "
        f"Agent `{agent.get('agent')}`"
        + (f", model `{agent['model']}`" if agent.get("model") else "")
        + ".",
        "",
        "| Arm | Runs | Task success | Knowledge lookups | Turns | Input tokens | Output tokens "
        "| Wall s | Agent errors | Cost (USD) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        s = report["arms"][arm]
        lines.append(
            f"| {arm} | {s['runs']} | {_pct(s['success_rate'])} | "
            f"{_num(s['mean_knowledge_lookups'])} | {_num(s['mean_turns'])} | "
            f"{_num(s['mean_input_tokens'])} | {_num(s['mean_output_tokens'])} | "
            f"{_num(s['mean_wall_s'])} | {s['agent_errors']} | {_num(s['total_cost_usd'])} |"
        )
    verdict = report["bar"]
    lines += ["", "## Bar: higher task success and fewer Knowledge lookups with context", ""]
    if verdict.get("met") is None:
        lines.append(f"Not evaluated: {verdict.get('reason')}.")
    else:
        lines.append(
            f"- Higher success with context: {verdict['higher_success_with_context']} "
            f"({verdict['success_delta'] * 100:+.1f} points)"
        )
        lines.append(
            f"- Fewer lookups with context: {verdict['fewer_lookups_with_context']} "
            f"({verdict['lookup_delta']:+g} per task)"
        )
        if verdict["success_at_ceiling"]:
            lines.append("- Both arms succeeded on every task, so success cannot be higher.")
        lines.append(f"- **Bar met: {verdict['met']}**")
    lines += [
        "",
        "## Per case",
        "",
        "| Case | Context chars | With context | Without context |",
        "|---|---|---|---|",
    ]
    for record in report["records"]:
        if record["error"]:
            continue
        cells = []
        for arm in ARMS:
            runs = [a for a in record["arms"] if a["arm"] == arm]
            passed = sum(1 for a in runs if a["passed"])
            lookups = sum(a["knowledge_lookups"] for a in runs)
            cells.append(f"{passed}/{len(runs)} passed, {lookups} lookups")
        lines.append(
            f"| `{record['case_id']}` | {record['context_chars']} | {cells[0]} | {cells[1]} |"
        )
    errored = [r for r in report["records"] if r["error"]]
    if errored:
        lines += ["", "## Errored cases", ""]
        lines += [f"- `{r['case_id']}`: {r['error']}" for r in errored]
    failures = [
        (r, a) for r in report["records"] if not r["error"] for a in r["arms"] if not a["passed"]
    ]
    if failures:
        lines += ["", "## Failed tasks", ""]
        for record, arm in failures:
            failed = [name for name, ok in arm["checks"].items() if not ok]
            why = arm["error"] or f"failed {', '.join(failed) or 'no checks'}"
            lines.append(
                f"- `{record['case_id']}` {arm['arm']} #{arm['repeat']}: {why}; "
                f"{json.dumps(arm['details'])}"
            )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"beakr-bench-{SUITE}-{report['run_id']}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    md_path.write_text(render_markdown(report))
    return json_path, md_path
