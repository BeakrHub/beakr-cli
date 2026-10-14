"""Results as JSON (for machines) and Markdown (for people).

Per suite: cases passed, asks passed, and for ``compounding`` the slope -- the
pass rate at each episode and the change from the first episode to the last,
which is the number that suite exists to measure. Errored cases are counted and
listed separately, never folded into a pass rate as failures: an unreachable
sandbox says nothing about the system under test.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from beakr_bench.runner import CaseRecord


def _rate(passed: int, total: int) -> float | None:
    return round(passed / total, 4) if total else None


def episode_curve(records: list[CaseRecord]) -> dict[str, Any]:
    by_episode: dict[int, list[bool]] = defaultdict(list)
    for record in records:
        if record.error:
            continue
        for ask in record.asks:
            if ask.episode is not None:
                by_episode[ask.episode].append(ask.passed)
    curve = {
        str(episode): _rate(sum(results), len(results))
        for episode, results in sorted(by_episode.items())
    }
    values = [value for value in curve.values() if value is not None]
    slope = round(values[-1] - values[0], 4) if len(values) >= 2 else None
    return {"pass_rate_by_episode": curve, "first_to_last_change": slope}


def summarise(suite: str, records: list[CaseRecord]) -> dict[str, Any]:
    scored = [r for r in records if r.error is None]
    asks = [a for r in scored for a in r.asks]
    summary: dict[str, Any] = {
        "suite": suite,
        "cases": len(records),
        "errored": len(records) - len(scored),
        "cases_passed": sum(1 for r in scored if r.passed),
        "case_pass_rate": _rate(sum(1 for r in scored if r.passed), len(scored)),
        "asks": len(asks),
        "ask_pass_rate": _rate(sum(1 for a in asks if a.passed), len(asks)),
    }
    if suite == "compounding":
        summary["slope"] = episode_curve(records)
    return summary


def build_report(results: dict[str, list[CaseRecord]], *, run_id: str, target: str) -> dict:
    return {
        "benchmark": "beakr-bench",
        "run_id": run_id,
        "target": target,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "summaries": [summarise(suite, records) for suite, records in results.items()],
        "cases": [asdict(record) for records in results.values() for record in records],
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# beakr-bench results",
        "",
        f"Run `{report['run_id']}` against `{report['target']}`, {report['generated_at']}.",
        "",
        "| Suite | Cases | Errored | Cases passed | Asks passed |",
        "|---|---|---|---|---|",
    ]
    for summary in report["summaries"]:
        lines.append(
            f"| {summary['suite']} | {summary['cases']} | {summary['errored']} | "
            f"{_pct(summary['case_pass_rate'])} | {_pct(summary['ask_pass_rate'])} |"
        )
    for summary in report["summaries"]:
        slope = summary.get("slope")
        if not slope:
            continue
        lines += ["", "## Compounding slope", "", "| Episode | Pass rate |", "|---|---|"]
        for episode, rate in slope["pass_rate_by_episode"].items():
            lines.append(f"| {episode} | {_pct(rate)} |")
        change = slope["first_to_last_change"]
        lines.append("")
        lines.append(
            "First to last episode: " + ("n/a" if change is None else f"{change * 100:+.1f} points")
        )
    failures = [
        (case, ask) for case in report["cases"] for ask in case["asks"] if not ask["passed"]
    ]
    errored = [case for case in report["cases"] if case["error"]]
    if errored:
        lines += ["", "## Errored cases", ""]
        lines += [f"- `{c['suite']}/{c['case_id']}`: {c['error']}" for c in errored]
    if failures:
        lines += ["", "## Failed asks", ""]
        for case, ask in failures:
            failed = [name for name, ok in ask["checks"].items() if not ok]
            lines.append(
                f"- `{case['suite']}/{case['case_id']}` {ask['label']}: "
                f"failed {', '.join(failed) or 'no checks'}; {json.dumps(ask['details'])}"
            )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"beakr-bench-{report['run_id']}.json"
    md_path = out_dir / f"beakr-bench-{report['run_id']}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    md_path.write_text(render_markdown(report))
    return json_path, md_path
