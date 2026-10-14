"""Incremental imports carry ``base_index``, and a 409 re-syncs the cursor.

Root cause these guard: the engine reads a batch WITHOUT ``base_index`` as the
whole transcript from position 0 (identical prefix kept, the rest replaced). The
hooks sent only the messages after their cursor and never sent ``base_index``,
so each incremental send replaced the stored history with just the tail. When
the engine holds fewer messages than ``base_index`` it answers 409 with
``expected_base_index``; the client resends from there.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
import respx
from beakr_sdk import Beakr
from typer.testing import CliRunner

from beakr_cli.hook_runtime import read_state, write_state
from beakr_cli.main import app
from tests.hook_keys import hook_key, identity_for

# These tests are about what capture sends, not whether a repository consents.
pytestmark = pytest.mark.usefixtures("capture_consented")

API = "https://api.test"
FIXTURE = Path(__file__).parent / "fixtures" / "transcripts" / "claude_code_session.jsonl"
OK = {"conversation_id": "conv-1", "created": False, "stored": "summary"}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _conflict(expected: int) -> httpx.Response:
    detail = {"code": "conflict", "message": "resend", "expected_base_index": expected}
    return httpx.Response(409, json={"detail": detail, "error": {**detail, "request_id": "r"}})


def _bodies(route) -> list[dict]:
    return [json.loads(call.request.content) for call in route.calls]


def _set_cursor(cursor: int) -> None:
    entry = {"cursor": cursor, "conversation_id": "conv-1"}
    write_state("hooks_state.json", {hook_key(API): entry})


@respx.mock
def test_sdk_import_sends_base_index():
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    with Beakr(base_url=API) as sdk:
        sdk.conversations.import_(
            client="codex",
            session_id="s",
            base_index=7,
            messages=[{"role": "user", "content": "x"}],
        )
    assert _bodies(route)[0]["base_index"] == 7


@respx.mock
def test_a_server_behind_the_cursor_gets_the_missing_messages(tmp_path: Path):
    transcript = tmp_path / "s.jsonl"
    shutil.copy(FIXTURE, transcript)
    _set_cursor(2)  # the hook believes 2 of the 3 messages were sent
    route = respx.post(f"{API}/v1/conversations/import").mock(
        side_effect=[_conflict(1), httpx.Response(200, json=OK)]
    )
    result = CliRunner().invoke(
        app, ["conversations", "import", "--from-transcript", str(transcript), "--json"]
    )
    assert result.exit_code == 0, result.output
    first, resend = _bodies(route)
    assert first["base_index"] == 2 and len(first["messages"]) == 1
    # Resent from what the server holds, filled from the local transcript.
    assert resend["base_index"] == 1 and len(resend["messages"]) == 2
    assert read_state("hooks_state.json")[hook_key(API)]["cursor"] == 3


@respx.mock
def test_a_queued_batch_refused_for_a_gap_rewinds_the_cursor(tmp_path: Path):
    from beakr_cli import hook_queue

    transcript = tmp_path / "s.jsonl"
    shutil.copy(FIXTURE, transcript)
    _set_cursor(3)
    hook_queue.enqueue(
        {
            "key": hook_key(API),
            "identity": identity_for(API).as_dict(),
            "body": {
                "client": "claude-code",
                "session_id": "cc-session-1",
                "base_index": 2,
                "messages": [{"role": "assistant", "content": "x"}],
            },
        }
    )
    route = respx.post(f"{API}/v1/conversations/import").mock(
        side_effect=[_conflict(1), httpx.Response(200, json=OK)]
    )
    respx.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
    result = CliRunner().invoke(
        app, ["compound", "--from-transcript", str(transcript), "--hook"], input=""
    )
    assert result.exit_code == 0, result.output
    assert hook_queue.queued_files() == []
    queued, resend = _bodies(route)
    assert queued["base_index"] == 2
    # The cursor rewound to the server's count, so the same run resent the rest.
    assert resend["base_index"] == 1 and len(resend["messages"]) == 2
    assert read_state("hooks_state.json")[hook_key(API)]["cursor"] == 3


@respx.mock
def test_an_idempotency_conflict_is_not_mistaken_for_a_gap(tmp_path: Path):
    transcript = tmp_path / "s.jsonl"
    shutil.copy(FIXTURE, transcript)
    respx.post(f"{API}/v1/conversations/import").respond(
        409,
        json={
            "detail": {"code": "idempotency_in_progress"},
            "error": {"code": "idempotency_in_progress"},
        },
    )
    result = CliRunner().invoke(
        app, ["conversations", "import", "--from-transcript", str(transcript)]
    )
    assert result.exit_code == 5


@respx.mock
def test_file_import_passes_its_base_index(tmp_path: Path):
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    file = tmp_path / "c.json"
    file.write_text(
        json.dumps(
            {"session_id": "s1", "base_index": 4, "messages": [{"role": "user", "content": "x"}]}
        )
    )
    result = CliRunner().invoke(app, ["conversations", "import", "--file", str(file), "--json"])
    assert result.exit_code == 0, result.output
    assert _bodies(route)[0]["base_index"] == 4


@respx.mock
def test_file_import_without_base_index_sends_none(tmp_path: Path):
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    file = tmp_path / "c.json"
    file.write_text(
        json.dumps({"session_id": "s1", "messages": [{"role": "user", "content": "x"}]})
    )
    assert CliRunner().invoke(app, ["conversations", "import", "--file", str(file)]).exit_code == 0
    assert "base_index" not in _bodies(route)[0]


@respx.mock
async def test_mcp_import_sends_base_index_and_explains_a_gap():
    from beakr_cli.mcp_tools_platform import import_conversation

    route = respx.post(f"{API}/v1/conversations/import").mock(
        side_effect=[httpx.Response(200, json={**OK, "created": True}), _conflict(5)]
    )
    message = [{"role": "user", "content": "x"}]
    text = await import_conversation(client="codex", session_id="s", messages=message, base_index=0)
    assert "conv-1" in text
    gap = await import_conversation(client="codex", session_id="s", messages=message, base_index=9)
    assert "position 5" in gap and "base_index=5" in gap
    assert [b["base_index"] for b in _bodies(route)] == [0, 9]
