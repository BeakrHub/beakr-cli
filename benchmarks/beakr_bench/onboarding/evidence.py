"""Evidence for a live run, written as it happens.

A live run takes an hour and costs money; an interruption must not erase the
runs that already finished, and a reviewer must be able to see what each agent
actually did, not only the score. ``EvidenceLog`` writes, under one directory:

- ``checkpoint.jsonl``: one line per finished arm run (its scored result,
  without the transcript), appended and flushed to disk as each run ends, plus
  one line per case with its seeding notes, context size and any error;
- ``runs/<case>/<arm>-r<repeat>.json``: the full run: prompt, the context the
  arm was given (or none), the answer, every tool call and the agent's
  transcript (model turns and tool results, or the client's raw event stream);
- ``cases/<case>.json``: the case's seeding notes and the injected context.

``load_checkpoint`` rebuilds the records from ``checkpoint.jsonl``, so a report
can be produced from an interrupted run (``--from-checkpoint``).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from beakr_bench.onboarding.agents import AgentRun
from beakr_bench.onboarding.harness import ArmResult, OnboardingRecord


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name) or "_"


class EvidenceLog:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.checkpoint = self.directory / "checkpoint.jsonl"

    def _append(self, entry: dict[str, Any]) -> None:
        with self.checkpoint.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def case_started(self, case_id: str, title: str, project_id: str | None) -> None:
        self._append({"kind": "case", "case_id": case_id, "title": title, "project_id": project_id})

    def context(self, case_id: str, prompt: str, context: str, notes: list[str]) -> None:
        path = self.directory / "cases" / f"{_safe(case_id)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"case_id": case_id, "prompt": prompt, "context": context, "notes": notes},
                       indent=2)
        )  # fmt: skip

    def run_finished(
        self,
        case_id: str,
        *,
        prompt: str,
        context: str | None,
        run: AgentRun,
        result: ArmResult,
    ) -> Path:
        path = self.directory / "runs" / _safe(case_id) / f"{result.arm}-r{result.repeat}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "prompt": prompt,
                    "context": context,
                    "result": asdict(result),
                    "stop_reason": run.stop_reason,
                    "transcript": run.transcript,
                },
                indent=2,
                default=str,
            )
        )
        self._append({"kind": "arm", "case_id": case_id, "result": asdict(result)})
        return path

    def case_finished(self, record: OnboardingRecord) -> None:
        self._append(
            {
                "kind": "case_done",
                "case_id": record.case_id,
                "context_chars": record.context_chars,
                "error": record.error,
                "notes": record.notes,
            }
        )


def load_checkpoint(path: Path) -> list[OnboardingRecord]:
    """The records a checkpoint holds, finished cases and the arm runs of an unfinished one."""
    arm_fields = {f.name for f in fields(ArmResult)}
    records: dict[str, OnboardingRecord] = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        case_id = entry["case_id"]
        if entry["kind"] == "case":
            records[case_id] = OnboardingRecord(
                case_id=case_id,
                title=entry.get("title", case_id),
                project_id=entry.get("project_id"),
            )
            continue
        record = records.setdefault(case_id, OnboardingRecord(case_id=case_id, title=case_id))
        if entry["kind"] == "arm":
            data = {k: v for k, v in entry["result"].items() if k in arm_fields}
            record.arms.append(ArmResult(**data))
        elif entry["kind"] == "case_done":
            record.context_chars = entry.get("context_chars", 0)
            record.error = entry.get("error")
            record.notes = list(entry.get("notes") or [])
    return list(records.values())
