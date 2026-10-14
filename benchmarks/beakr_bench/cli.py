"""``beakr-bench``: run the public benchmark suites against a sandbox org.

    python -m beakr_bench --suite all --out results/
    python -m beakr_bench --suite corrections --dry-run

The key comes from ``BEAKR_API_KEY`` (or the CLI's sign-in, as any SDK client).
Runs refuse a live key (``bk_live_``) unless ``--allow-live`` is passed: every
case creates and deletes projects, so it belongs in a sandbox.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from beakr_bench import SUITES
from beakr_bench.cases import load_suite
from beakr_bench.judge import load_judge
from beakr_bench.report import build_report, render_markdown, write_report
from beakr_bench.runner import CaseRecord, CaseRunner, RunOptions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="beakr-bench", description=__doc__.split("\n\n")[0])
    parser.add_argument("--suite", action="append", choices=[*SUITES, "all"], default=None)
    parser.add_argument("--case", action="append", help="Run only these case ids.")
    parser.add_argument("--out", type=Path, default=Path("beakr-bench-results"))
    parser.add_argument("--dry-run", action="store_true", help="Offline fake engine; no network.")
    parser.add_argument("--allow-live", action="store_true", help="Permit a bk_live_ key.")
    parser.add_argument("--keep-projects", action="store_true", help="Do not delete projects.")
    parser.add_argument("--compound-timeout", type=float, default=600.0)
    parser.add_argument("--judge", help="Optional judge 'module:callable' (off by default).")
    return parser


def _live_client(allow_live: bool):
    from beakr_sdk import Beakr

    key = os.environ.get("BEAKR_API_KEY", "")
    if key.startswith("bk_live_") and not allow_live:
        raise SystemExit(
            "Refusing to run with a live key: beakr-bench creates and deletes projects. "
            "Use a sandbox org's bk_test_ key, or pass --allow-live."
        )
    client = Beakr()
    return client, client.base_url


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    suites = SUITES if not args.suite or "all" in args.suite else tuple(args.suite)
    if args.dry_run:
        from beakr_bench.dryrun import DRY_RUN_BASE_URL, dry_run_client

        client, _engine = dry_run_client()
        target = DRY_RUN_BASE_URL
    else:
        client, target = _live_client(args.allow_live)
    options = RunOptions(
        keep_projects=args.keep_projects,
        compound_timeout_s=args.compound_timeout,
        judge=load_judge(args.judge),
    )
    if args.dry_run:
        options.sleep = lambda _seconds: None
    runner = CaseRunner(client, options)
    results: dict[str, list[CaseRecord]] = {}
    for suite in suites:
        _description, cases = load_suite(suite)
        if args.case:
            cases = [case for case in cases if case.case_id in args.case]
        results[suite] = [runner.run(case) for case in cases]
    report = build_report(results, run_id=options.run_id, target=str(target))
    json_path, md_path = write_report(report, args.out)
    sys.stdout.write(render_markdown(report))
    sys.stdout.write(f"\nWrote {json_path} and {md_path}\n")
    errored = any(summary["errored"] for summary in report["summaries"])
    return 2 if errored else 0


if __name__ == "__main__":
    raise SystemExit(main())
