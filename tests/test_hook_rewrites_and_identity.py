"""Rewritten transcripts are resent whole, and hook state belongs to one sign-in.

Root causes guarded (Codex review C012):

- A compacted (shorter) transcript only "waited for growth", and a message
  edited in place at the same length went unnoticed, so the engine kept a
  history the agent no longer had and new content after a compaction was
  skipped. The hook now keeps a hash per sent message and resends the whole
  transcript without ``base_index`` when one no longer matches.
- Hook state and queued payloads were keyed only by client and session, and a
  queued private transcript was flushed with whichever sign-in was current, so
  after a login or org switch it could be uploaded with someone else's
  credentials.
"""

from __future__ import annotations

import json
import stat

import httpx
import pytest
import respx
from beakr_sdk import Beakr
from beakr_sdk.credentials import StoredTokens, TokenStore, beakr_home

from beakr_cli import compounding, hook_queue
from beakr_cli.compounding import import_and_compound
from beakr_cli.hook_runtime import read_state
from beakr_cli.transcripts import ParsedSession
from tests.hook_keys import hook_key, identity_for

API = "https://api.test"
OK = {"conversation_id": "conv-1", "created": False, "stored": "summary"}


@pytest.fixture(autouse=True)
def signed_in(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_alice")


def _messages(*texts: str) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(texts)
    ]


def _session(*texts: str) -> ParsedSession:
    return ParsedSession("s1", messages=_messages(*texts))


def _run(session: ParsedSession, *, hook: bool = True):
    with Beakr(base_url=API) as sdk:
        return import_and_compound(
            sdk, session, client="claude-code", compound=False, queue_on_failure=hook
        )


def _bodies(route) -> list[dict]:
    return [json.loads(call.request.content) for call in route.calls]


@respx.mock
def test_an_edit_in_place_resends_the_whole_transcript() -> None:
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    _run(_session("a", "b", "c"))
    _run(_session("a", "B (edited)", "c"))
    first, second = _bodies(route)
    assert first["base_index"] == 0
    assert "base_index" not in second  # the engine keeps the matching prefix
    assert [m["content"] for m in second["messages"]] == ["a", "B (edited)", "c"]


@respx.mock
def test_a_compacted_transcript_is_resent_not_waited_on() -> None:
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    _run(_session("a", "b", "c", "d"))
    _run(_session("summary of a-d", "e"))
    second = _bodies(route)[1]
    assert "base_index" not in second
    assert [m["content"] for m in second["messages"]] == ["summary of a-d", "e"]
    entry = read_state("hooks_state.json")[hook_key(API, "s1")]
    assert entry["cursor"] == 2 and len(entry["hashes"]) == 2


@respx.mock
def test_appends_after_a_rewrite_go_back_to_base_index() -> None:
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    _run(_session("a", "b"))
    _run(_session("x"))
    _run(_session("x", "y"))
    assert _bodies(route)[2]["base_index"] == 1


@respx.mock
def test_large_sends_are_batched_below_the_engine_cap(monkeypatch) -> None:
    monkeypatch.setattr(compounding, "MAX_BATCH_MESSAGES", 2)
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    _run(_session("a", "b", "c", "d", "e"))
    assert [(b.get("base_index"), len(b["messages"])) for b in _bodies(route)] == [
        (0, 2),
        (2, 2),
        (4, 1),
    ]
    _run(_session("A", "b", "c", "d", "e"))  # rewrite: whole transcript, still batched
    assert [(b.get("base_index"), len(b["messages"])) for b in _bodies(route)[3:]] == [
        (None, 2),
        (2, 2),
        (4, 1),
    ]


@respx.mock
def test_a_rewrite_supersedes_the_sessions_queued_batches() -> None:
    respx.post(f"{API}/v1/conversations/import").mock(side_effect=httpx.ConnectError("x"))
    _run(_session("a", "b"))
    assert len(hook_queue.queued_files()) == 1
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    # respx merges identical routes, so skip the failed attempt already recorded.
    before = len(route.calls)
    _run(_session("a2", "b"))
    assert hook_queue.queued_files() == []
    (body,) = _bodies(route)[before:]
    assert "base_index" not in body and body["messages"][0]["content"] == "a2"


@respx.mock
def test_queued_imports_are_never_sent_by_another_sign_in(monkeypatch) -> None:
    respx.post(f"{API}/v1/conversations/import").mock(side_effect=httpx.ConnectError("x"))
    _run(_session("alice's private turn"))
    assert len(hook_queue.queued_files()) == 1

    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_bob")
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    # respx merges identical routes, so skip Alice's failed attempt already recorded.
    before = len(route.calls)
    _run(_session("bob's turn"))
    sent = [m["content"] for body in _bodies(route)[before:] for m in body["messages"]]
    assert sent == ["bob's turn"]
    # Alice's payload is kept for Alice, not sent and not lost.
    (kept,) = hook_queue.queued_files()
    assert json.loads(kept.read_text())["identity"] == identity_for_key("bk_live_alice")


def identity_for_key(key: str) -> dict:
    import os

    previous = os.environ["BEAKR_API_KEY"]
    os.environ["BEAKR_API_KEY"] = key
    try:
        return identity_for(API).as_dict()
    finally:
        os.environ["BEAKR_API_KEY"] = previous


@respx.mock
def test_state_is_separate_per_api_and_principal(monkeypatch) -> None:
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    _run(_session("a", "b"))
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_bob")
    _run(_session("a", "b"))
    # Bob's run starts from his own (empty) cursor, not Alice's.
    assert [b["base_index"] for b in _bodies(route)] == [0, 0]
    keys = [k for k in read_state("hooks_state.json") if not k.startswith("_")]
    assert len(keys) == 2


def test_a_payload_without_a_recorded_sign_in_is_dropped_not_sent() -> None:
    hook_queue.enqueue({"key": "claude-code:s1", "body": {"messages": [{"role": "user"}]}})
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{API}/v1/conversations/import").respond(json=OK)
        _run(_session("a"))
    assert [len(b["messages"]) for b in _bodies(route)] == [1]
    assert hook_queue.queued_files() == []


@respx.mock
def test_queue_and_state_files_are_owner_only() -> None:
    respx.post(f"{API}/v1/conversations/import").mock(side_effect=httpx.ConnectError("x"))
    _run(_session("private"))
    (queued,) = hook_queue.queued_files()
    assert stat.S_IMODE(queued.stat().st_mode) == 0o600
    assert stat.S_IMODE(queued.parent.stat().st_mode) == 0o700
    state = beakr_home() / "hooks_state.json"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600


@respx.mock
def test_a_login_without_a_known_user_looks_it_up_once(monkeypatch) -> None:
    monkeypatch.delenv("BEAKR_API_KEY")
    TokenStore().save(
        StoredTokens(
            access_token="bk_at_1",
            refresh_token="bk_rt_1",
            expires_at=4_000_000_000,
            base_url=API,
            org_id="org-1",
        )
    )
    me = respx.get(f"{API}/v1/me").respond(json={"user": {"id": "user-7"}})
    respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    _run(_session("a"))
    _run(_session("a", "b"))
    assert me.call_count == 1
    assert "user:user-7:org=org-1" in hook_key(API, "s1")


@respx.mock
def test_an_unknown_sign_in_sends_nothing(monkeypatch) -> None:
    monkeypatch.delenv("BEAKR_API_KEY")
    TokenStore().save(
        StoredTokens(
            access_token="bk_at_1", refresh_token="bk_rt_1", expires_at=4_000_000_000, base_url=API
        )
    )
    respx.get(f"{API}/v1/me").mock(side_effect=httpx.ConnectError("offline"))
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    outcome = _run(_session("a"))
    assert not route.called and not outcome.imported and not outcome.queued
    assert hook_queue.queued_files() == []
