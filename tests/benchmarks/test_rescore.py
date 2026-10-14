"""Rescoring writes a labelled copy and never alters the original (C066 provenance)."""

from __future__ import annotations

import hashlib
import json

import pytest
from beakr_bench.rescore import RescoreError, rescore
from beakr_bench.scoring import SCORER_VERSION


def _suite_report(answer: str, passed: bool) -> dict:
    return {
        "benchmark": "beakr-bench",
        "run_id": "abc12345",
        "target": "http://localhost:8099",
        "generated_at": "2026-10-10T14:58:16+00:00",
        "summaries": [],
        "cases": [
            {
                "suite": "compounding",
                "case_id": "kestrel-batch-release",
                "title": "t",
                "project_id": "p",
                "asks": [
                    {
                        "label": "e1-release",
                        "question": "Was lot KB-2207 released?",
                        "via": "research",
                        "episode": 1,
                        "passed": passed,
                        "checks": {"must_not_include": passed},
                        "details": {},
                        "answer": answer,
                        "source_count": 1,
                        "judge": None,
                    }
                ],
                "error": None,
                "notes": [],
            }
        ],
    }


def test_rescoring_labels_the_copy_and_leaves_the_original(tmp_path):
    source = tmp_path / "beakr-bench-abc12345.json"
    source.write_text(
        json.dumps(_suite_report("I can't confirm whether KB-2207 was released.", False))
    )
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    written = rescore(source)
    assert written.name == "beakr-bench-abc12345.rescored.json"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    copy = json.loads(written.read_text())
    meta = copy["rescore"]
    assert meta["original"] == {
        "file": source.name,
        "sha256": before,
        "run_id": "abc12345",
        "generated_at": "2026-10-10T14:58:16+00:00",
    }
    assert meta["scorer"]["version"] == SCORER_VERSION and len(meta["scorer"]["sha256"]) == 64
    assert set(meta["case_definitions"]) == {"compounding.json"}
    assert meta["rescored_at"]
    assert meta["changes"][0]["before"]["passed"] is False
    assert meta["changes"][0]["after"]["passed"] is True
    assert copy["summaries"][0]["ask_pass_rate"] == 1.0


def test_an_asserted_claim_stays_failed_after_rescoring(tmp_path):
    source = tmp_path / "beakr-bench-r.json"
    source.write_text(json.dumps(_suite_report("KB-2207 was released.", False)))
    copy = json.loads(rescore(source).read_text())
    assert copy["cases"][0]["asks"][0]["passed"] is False
    assert copy["rescore"]["changes"][0]["after"]["checks"] == {"must_not_assert": False}


def test_rescoring_refuses_to_overwrite_or_chain(tmp_path):
    source = tmp_path / "beakr-bench-r.json"
    source.write_text(json.dumps(_suite_report("x", True)))
    with pytest.raises(RescoreError):
        rescore(source, out=source)
    chained = tmp_path / "beakr-bench-r.rescored.json"
    chained.write_text(source.read_text())
    with pytest.raises(RescoreError):
        rescore(chained)


def test_onboarding_reports_recompute_the_bar(tmp_path):
    def arm(name: str, answer: str, passed: bool, lookups: int) -> dict:
        return {
            "arm": name, "repeat": 0, "passed": passed, "checks": {}, "details": {},
            "knowledge_lookups": lookups, "turns": 1, "input_tokens": 1, "output_tokens": 1,
            "cache_read_tokens": 0, "wall_s": 1.0, "cost_usd": 0.01, "error": None,
            "answer": answer, "tools": [], "models_served": [],
        }  # fmt: skip

    good = "```python\nz_prime >= 0.5\n```\nNot the old `z_prime >= 0.4`."
    report = {
        "benchmark": "beakr-bench",
        "suite": "agent_onboarding",
        "run_id": "onb1",
        "generated_at": "2026-10-10T15:00:00+00:00",
        "arms": {},
        "bar": {},
        "records": [
            {
                "case_id": "corrected-threshold",
                "title": "t",
                "project_id": "p",
                "context_chars": 1,
                "arms": [
                    arm("with_context", good, False, 1),
                    arm("without_context", "```python\nz_prime >= 0.4\n```", False, 2),
                ],
                "error": None,
                "notes": [],
            }
        ],
    }
    source = tmp_path / "beakr-bench-agent_onboarding-onb1.json"
    source.write_text(json.dumps(report))
    copy = json.loads(rescore(source).read_text())
    assert copy["arms"]["with_context"]["success_rate"] == 1.0
    assert copy["arms"]["without_context"]["success_rate"] == 0.0
    assert copy["bar"]["met"] is True
    assert [c["arm"] for c in copy["rescore"]["changes"]] == ["with_context", "without_context"]


def _v2_report(case_file: dict | None) -> dict:
    answer = "Severity: Sev‑2. Page Priya Nandakumar, the secondary on-call."
    report = {
        "benchmark": "beakr-bench",
        "suite": "agent_onboarding",
        "run_id": "v2run",
        "generated_at": "2026-10-10T00:00:00+00:00",
        "records": [
            {
                "case_id": "sev2-paging",
                "error": None,
                "arms": [
                    {
                        "arm": "with_context",
                        "repeat": 0,
                        "passed": False,
                        "checks": {"must_include": False},
                        "details": {},
                        "knowledge_lookups": 1,
                        "turns": 1,
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cache_read_tokens": 0,
                        "wall_s": 1.0,
                        "cost_usd": 0.01,
                        "error": None,
                        "answer": answer,
                        "tools": [],
                        "models_served": [],
                    }  # fmt: skip
                ],
            }
        ],
    }
    if case_file is not None:
        report["case_file"] = case_file
    return report


def test_a_preregistered_case_file_is_used_and_its_hash_recorded(tmp_path):
    # Root cause: rescoring always loaded the default case file, so a v2 report
    # (run with --case-file) could not be rescored against its own definitions.
    path = "benchmarks/beakr_bench/data/agent_onboarding_v2.json"
    from beakr_bench.rescore import REPO_ROOT

    digest = hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest()
    source = tmp_path / "report.json"
    source.write_text(json.dumps(_v2_report({"path": path, "sha256": digest})))
    written = json.loads(rescore(source).read_text())
    assert written["rescore"]["case_definitions"] == {"agent_onboarding_v2.json": digest}
    arm = written["records"][0]["arms"][0]
    assert arm["passed"] is True  # U+2011 now matches "Sev-2" (C068)


def test_a_case_file_changed_since_the_run_is_refused(tmp_path):
    path = "benchmarks/beakr_bench/data/agent_onboarding_v2.json"
    source = tmp_path / "report.json"
    source.write_text(json.dumps(_v2_report({"path": path, "sha256": "0" * 64})))
    with pytest.raises(RescoreError, match="changed since the run"):
        rescore(source)
