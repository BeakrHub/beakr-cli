"""Capture paths that mean to compound ask the engine to hold the text, and say truly
whether a capture was queued.

Root cause (Codex review C086): the engine keeps transcript text only by opt-in
(C084). Capture imported with ``store_full_text=false`` and no flag, then
called ``/v1/compound`` separately, so the text was already gone and the engine
answered ``nothing_to_compound``; capture ignored that answer, reported
``compounded=true`` and advanced its counters. Now every batch of a session
meant to be compounded carries ``hold_text``, the batch that compounds now
carries ``compound``, and a ``nothing_to_compound`` answer is not counted.
The engine-side flow is tested in beakr-engine's
``tests/api/test_cli_compound_text_hold_flow.py``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from beakr_sdk import Beakr

from beakr_cli import compounding, hook_queue
from beakr_cli.compounding import import_and_compound
from beakr_cli.hook_runtime import read_state, write_state
from beakr_cli.transcripts import ParsedSession
from tests.hook_keys import hook_key

pytestmark = pytest.mark.usefixtures("capture_consented")

API = "https://api.test"
OK = {"conversation_id": "conv-1", "created": False, "stored": "summary"}


@pytest.fixture(autouse=True)
def signed_in(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_alice")


def _session(*texts: str) -> ParsedSession:
    roles = ("user", "assistant")
    return ParsedSession(
        "s1", messages=[{"role": roles[i % 2], "content": t} for i, t in enumerate(texts)]
    )


def _run(session: ParsedSession, **kwargs):
    with Beakr(base_url=API) as sdk:
        return import_and_compound(sdk, session, client="claude-code", **kwargs)


def _bodies(route) -> list[dict]:
    return [json.loads(call.request.content) for call in route.calls]


def _entry() -> dict:
    return read_state("hooks_state.json").get(hook_key(API, "s1"), {})


@respx.mock
def test_only_the_last_batch_queues_the_capture_and_every_batch_holds_text(monkeypatch):
    monkeypatch.setattr(compounding, "MAX_BATCH_MESSAGES", 2)
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    respx.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
    outcome = _run(_session("a", "b", "c", "d", "e"), force=True)
    assert outcome.compounded is True
    bodies = _bodies(route)
    assert [b.get("hold_text") for b in bodies] == [True, True, True]
    assert [b.get("compound") for b in bodies] == [None, None, True]
    # Never an opt-in: left out, so only the person's own per-client setting
    # could keep the text (PROSE-392).
    assert all("store_full_text" not in b for b in bodies)


@respx.mock
def test_an_import_only_run_neither_holds_nor_compounds():
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    _run(_session("a", "b"), compound=False)
    (body,) = _bodies(route)
    assert "hold_text" not in body and "compound" not in body


@respx.mock
def test_a_throttled_run_holds_text_without_queuing_a_capture():
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    compounded = respx.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
    _run(_session("a", "b"), now=1_000.0)
    outcome = _run(_session("a", "b", "c"), now=1_010.0)
    assert outcome.compounded is False and outcome.reason == "compounded recently"
    throttled = _bodies(route)[-1]
    assert throttled.get("hold_text") is True and "compound" not in throttled
    assert compounded.call_count == 1


@respx.mock
def test_nothing_to_compound_is_not_reported_or_counted_as_compounded():
    respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    respx.post(f"{API}/v1/compound").respond(
        202, json={"status": "nothing_to_compound", "conversation_id": "conv-1"}
    )
    outcome = _run(_session("a", "b"), force=True)
    assert outcome.imported is True
    assert outcome.compounded is False
    assert outcome.reason == "the engine holds no unread text to compound"
    entry = _entry()
    assert "compounded_count" not in entry and "compounded_at" not in entry


@respx.mock
def test_a_queued_retry_keeps_the_hold_but_not_the_capture_request():
    respx.post(f"{API}/v1/conversations/import").mock(side_effect=httpx.ConnectError("offline"))
    outcome = _run(_session("a", "b"), force=True, queue_on_failure=True)
    assert outcome.queued is True
    (path,) = hook_queue.queued_files()
    body = hook_queue.read_payload(path)["body"]
    assert body["hold_text"] is True and "compound" not in body


@respx.mock
def test_a_resync_after_a_gap_still_queues_the_capture_on_the_last_batch():
    detail = {"code": "conflict", "message": "resend", "expected_base_index": 0}
    route = respx.post(f"{API}/v1/conversations/import").mock(
        side_effect=[
            httpx.Response(409, json={"detail": detail, "error": {**detail, "request_id": "r"}}),
            httpx.Response(200, json=OK),
        ]
    )
    respx.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
    write_state("hooks_state.json", {hook_key(API, "s1"): {"cursor": 1}})
    _run(_session("a", "b"), force=True)
    first, resend = _bodies(route)
    assert first["compound"] is True
    assert resend["base_index"] == 0 and resend["compound"] is True
    assert resend["hold_text"] is True
