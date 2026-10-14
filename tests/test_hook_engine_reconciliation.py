"""The real hook payloads, reconciled by the engine's real merge.

Root cause this guards (Codex review C012): the hook and the engine were each
tested against a mock of the other. The mocks agreed that the engine appends,
but the engine reads a batch without ``base_index`` as the whole transcript, so
the second Stop hook replaced the stored history with just the new tail. Here
the CLI builds its requests exactly as in a hook run, and the engine's own
``_merge`` (run in the engine's Python, see ``fixtures/engine_merge_bridge.py``)
decides what the server holds.

Needs a real engine checkout with its ``.venv`` (``tests._engine.real_engine``):
skipped in this repository's CI, which has only the engine snapshot, and run by
the engine's CI.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from beakr_sdk import Beakr
from beakr_sdk.errors import APIConnectionError, ConflictError

from beakr_cli import hook_queue
from beakr_cli.compounding import import_and_compound
from beakr_cli.transcripts import ParsedSession
from tests._engine import real_engine

API = "https://api.test"
BRIDGE = Path(__file__).parent / "fixtures" / "engine_merge_bridge.py"


class Engine:
    """The engine's merge, plus a switch that makes the network fail."""

    def __init__(self) -> None:
        engine = real_engine("beakr/services/conversations/importing.py")
        env = {**os.environ, "PYTHONPATH": str(engine)}
        self._process = subprocess.Popen(
            [str(engine / ".venv" / "bin" / "python"), str(BRIDGE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=engine,
            env=env,
        )
        self.offline = False
        self.held: list[str] = []
        self.requests: list[dict] = []

    def _ask(self, request: dict) -> dict:
        assert self._process.stdin and self._process.stdout
        self._process.stdin.write(json.dumps(request) + "\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        assert line, "engine bridge exited"
        return json.loads(line)

    def import_(self, **body):
        if self.offline:
            raise APIConnectionError("offline")
        self.requests.append(body)
        reply = self._ask({"base_index": body.get("base_index"), "messages": body["messages"]})
        if "conflict" in reply:
            detail = {"code": "conflict", "expected_base_index": reply["conflict"]}
            raise ConflictError("gap", status=409, body={"detail": detail, "error": detail})
        self.held = reply["held"]
        return {"conversation_id": "conv-1"}

    def fall_behind(self, count: int) -> None:
        self.held = self._ask({"reset_to": count})["held"]

    def close(self) -> None:
        if self._process.stdin:
            self._process.stdin.close()
        self._process.wait(timeout=10)


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    server = Engine()
    yield server
    server.close()


def _hook(engine: Engine, *texts: str):
    roles = ("user", "assistant")
    session = ParsedSession(
        "s1", messages=[{"role": roles[i % 2], "content": t} for i, t in enumerate(texts)]
    )
    with Beakr(base_url=API) as sdk:
        sdk.conversations.import_ = engine.import_
        return import_and_compound(
            sdk, session, client="claude-code", compound=False, queue_on_failure=True
        )


def test_repeated_stop_hooks_build_the_whole_history(engine) -> None:
    _hook(engine, "m0", "m1")
    _hook(engine, "m0", "m1", "m2", "m3")
    _hook(engine, "m0", "m1", "m2", "m3", "m4")
    assert engine.held == ["m0", "m1", "m2", "m3", "m4"]
    assert [r.get("base_index") for r in engine.requests] == [0, 2, 4]


def test_a_replayed_hook_sends_nothing(engine) -> None:
    _hook(engine, "m0", "m1")
    _hook(engine, "m0", "m1")
    assert len(engine.requests) == 1 and engine.held == ["m0", "m1"]


def test_a_queued_retry_lands_in_order(engine) -> None:
    _hook(engine, "m0")
    engine.offline = True
    _hook(engine, "m0", "m1", "m2")
    assert len(hook_queue.queued_files()) == 1
    engine.offline = False
    _hook(engine, "m0", "m1", "m2", "m3")
    assert engine.held == ["m0", "m1", "m2", "m3"]
    assert hook_queue.queued_files() == []


def test_a_compaction_replaces_the_history_it_rewrote(engine) -> None:
    _hook(engine, "m0", "m1", "m2", "m3")
    _hook(engine, "summary of m0-m3", "m4")
    assert engine.held == ["summary of m0-m3", "m4"]


def test_an_edit_keeps_the_unchanged_prefix(engine) -> None:
    _hook(engine, "m0", "m1", "m2")
    _hook(engine, "m0", "m1", "m2 (edited)")
    assert engine.held == ["m0", "m1", "m2 (edited)"]
    _hook(engine, "m0", "m1", "m2 (edited)", "m3")
    assert engine.held == ["m0", "m1", "m2 (edited)", "m3"]


def test_a_server_behind_the_cursor_is_filled_from_the_transcript(engine) -> None:
    _hook(engine, "m0", "m1", "m2")
    engine.fall_behind(1)  # e.g. a batch the server lost
    _hook(engine, "m0", "m1", "m2", "m3")
    assert engine.held == ["m0", "m1", "m2", "m3"]
