"""Re-score a saved benchmark report with the current checks, without touching the original.

    uv run python -m beakr_bench.rescore results/beakr-bench-295f5e38.json
    uv run python -m beakr_bench.rescore results/beakr-bench-agent_onboarding-3f74f91a.json

Every check is a pure function of the stored answer, so a report can be scored
again after a check's meaning changes, without calling a model. The result is a
separate ``<name>.rescored.json``. The original is never rewritten, and the
rescored copy says exactly what it is:

- ``rescore.original``: the original's file name, its SHA-256, run id and
  ``generated_at``;
- ``rescore.scorer``: ``SCORER_VERSION`` and the SHA-256 of ``scoring.py``;
- ``rescore.case_definitions``: the SHA-256 of each case file used;
- ``rescore.rescored_at``: when it was rescored;
- ``rescore.changes``: every ask or arm whose result changed, before and after.

Summaries and the onboarding bar are recomputed from the rescored results with
the same functions the live run used. Seeding, the answers, tool calls and costs
are copied unchanged: this is the same run under revised rules, not a new run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from beakr_bench import scoring
from beakr_bench.cases import DATA_DIR, load_suite
from beakr_bench.report import summarise
from beakr_bench.runner import AskRecord, CaseRecord
from beakr_bench.scoring import SCORER_VERSION, score_answer

RESCORED_SUFFIX = ".rescored.json"


class RescoreError(ValueError):
    """The report cannot be rescored as asked."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: The beakr-cli checkout: report case-file paths are recorded relative to it.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _provenance(
    source: Path, report: dict[str, Any], case_files: dict[str, Path]
) -> dict[str, Any]:
    return {
        "original": {
            "file": source.name,
            "sha256": _sha256(source),
            "run_id": report.get("run_id"),
            "generated_at": report.get("generated_at"),
        },
        "scorer": {
            "version": SCORER_VERSION,
            "change": scoring.SCORER_CHANGE,
            "sha256": _sha256(Path(scoring.__file__)),
        },
        "case_definitions": {name: _sha256(path) for name, path in sorted(case_files.items())},
        "rescored_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": (
            "Same run, same stored answers, scored again under the scorer and case "
            "definitions named here. Not a new run; the original report is unchanged."
        ),
    }


def _rescore_suites(report: dict[str, Any]) -> tuple[dict[str, Any], list[dict], list[str]]:
    checks_by_ask: dict[tuple[str, str, str], dict[str, Any]] = {}
    suites = sorted({case["suite"] for case in report["cases"]})
    for suite in suites:
        _, cases = load_suite(suite)
        for case in cases:
            for ask in case.asks:
                checks_by_ask[(suite, case.case_id, ask["label"])] = ask["checks"]
    changes: list[dict] = []
    records: dict[str, list[CaseRecord]] = {}
    for case in report["cases"]:
        asks = []
        for ask in case.get("asks", []):
            key = (case["suite"], case["case_id"], ask["label"])
            checks = checks_by_ask.get(key)
            if checks is None:
                raise RescoreError(f"no current case definition for {key}")
            # Sources are not stored, only their count; citation checks need only that.
            result = score_answer(ask["answer"], [None] * int(ask.get("source_count", 0)), checks)
            if result.passed != ask["passed"] or result.checks != ask["checks"]:
                changes.append(
                    {
                        "suite": case["suite"],
                        "case_id": case["case_id"],
                        "label": ask["label"],
                        "before": {"passed": ask["passed"], "checks": ask["checks"]},
                        "after": {"passed": result.passed, "checks": result.checks},
                    }
                )
            asks.append(
                AskRecord(
                    **{
                        **ask,
                        "passed": result.passed,
                        "checks": result.checks,
                        "details": result.details,
                    }
                )
            )
        record = CaseRecord(
            suite=case["suite"],
            case_id=case["case_id"],
            title=case["title"],
            project_id=case.get("project_id"),
            asks=asks,
            error=case.get("error"),
            notes=list(case.get("notes", [])),
        )
        records.setdefault(case["suite"], []).append(record)
    rescored = {
        **report,
        "summaries": [summarise(suite, rows) for suite, rows in records.items()],
        "cases": [asdict(record) for rows in records.values() for record in rows],
    }
    return rescored, changes, suites


def _onboarding_case_path(report: dict[str, Any]) -> Path:
    """The case file the run used: the report's recorded ``case_file``, hash-checked.

    A pre-registered variant set (``--case-file``) must be rescored against that
    same file, never the default one; a file that changed since the run is
    refused rather than silently scored against different definitions.
    """
    from beakr_bench.onboarding import SUITE
    from beakr_bench.onboarding.cases import DATA_DIR as ONBOARDING_DATA

    recorded = report.get("case_file")
    if not recorded:
        return ONBOARDING_DATA / f"{SUITE}.json"
    path = Path(recorded["path"])
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        raise RescoreError(f"the run's case file {recorded['path']} is missing")
    if recorded.get("sha256") and _sha256(path) != recorded["sha256"]:
        raise RescoreError(
            f"{recorded['path']} changed since the run (sha256 differs from the report's)"
        )
    return path


def _rescore_onboarding(
    report: dict[str, Any],
) -> tuple[dict[str, Any], list[dict], dict[str, Path]]:
    from beakr_bench.onboarding import ARMS
    from beakr_bench.onboarding.cases import load_cases
    from beakr_bench.onboarding.harness import ArmResult
    from beakr_bench.onboarding.report import arm_summary, bar

    case_path = _onboarding_case_path(report)
    _, cases = load_cases(path=case_path)
    checks_by_case = {case.case_id: case.checks for case in cases}
    arm_fields = {f.name for f in fields(ArmResult)}
    changes: list[dict] = []
    records = []
    all_arms: list[ArmResult] = []
    for record in report["records"]:
        checks = checks_by_case.get(record["case_id"])
        if checks is None:
            raise RescoreError(f"no current case definition for {record['case_id']}")
        arms = []
        for arm in record.get("arms", []):
            result = score_answer(arm.get("answer") or "", [], checks)
            passed = result.passed and arm.get("error") is None
            if passed != arm["passed"] or result.checks != arm["checks"]:
                changes.append(
                    {
                        "case_id": record["case_id"],
                        "arm": arm["arm"],
                        "repeat": arm["repeat"],
                        "before": {"passed": arm["passed"], "checks": arm["checks"]},
                        "after": {"passed": passed, "checks": result.checks},
                    }
                )
            updated = {
                **arm,
                "passed": passed,
                "checks": result.checks,
                "details": result.details,
            }
            arms.append(updated)
            if record.get("error") is None:
                all_arms.append(ArmResult(**{k: v for k, v in updated.items() if k in arm_fields}))
        records.append({**record, "arms": arms})
    summaries = {arm: arm_summary([a for a in all_arms if a.arm == arm]) for arm in ARMS}
    rescored = {**report, "arms": summaries, "bar": bar(summaries), "records": records}
    return rescored, changes, {case_path.name: case_path}


def rescore(source: Path, out: Path | None = None) -> Path:
    """Write ``<source>.rescored.json`` (or ``out``); return its path."""
    source = source.resolve()
    if source.name.endswith(RESCORED_SUFFIX):
        raise RescoreError("rescore the original report, not a rescored copy")
    target = (out or source.with_name(source.stem + RESCORED_SUFFIX)).resolve()
    if target == source:
        raise RescoreError("refusing to overwrite the original report")
    report = json.loads(source.read_text())
    if "records" in report:
        rescored, changes, case_files = _rescore_onboarding(report)
    elif "cases" in report and isinstance(report["cases"], list):
        rescored, changes, suites = _rescore_suites(report)
        case_files = {f"{suite}.json": DATA_DIR / f"{suite}.json" for suite in sorted(set(suites))}
    else:
        raise RescoreError(f"{source.name} is not a beakr-bench report")
    rescored["rescore"] = {**_provenance(source, report, case_files), "changes": changes}
    target.write_text(json.dumps(rescored, indent=2, default=str) + "\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="beakr-bench-rescore", description=__doc__.split("\n")[0])
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    for path in args.reports:
        written = rescore(path)
        summary = json.loads(written.read_text())["rescore"]
        sys.stdout.write(f"{written} ({len(summary['changes'])} result(s) changed)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
