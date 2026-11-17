"""`beakr mcp` under Codex works in the thread's session, the one its shell commands use.

Root cause (16 Nov, agent-102-codex): Codex starts `beakr mcp` without its thread id in
the environment, while the shell commands it runs have CODEX_THREAD_ID. So the MCP
server started a session of its own (no conversation id) and the shell another: a
lock taken through one did not hold in the other, and a restarted server started yet
another session. Codex names its thread on every MCP request
(`_meta["x-codex-turn-metadata"].thread_id`); the server now adopts it before its
first tool call, ending its own untouched session and joining the thread's.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import respx

from beakr_cli import scope_session
from beakr_cli.scope_session import McpSession, TokenState

API = "https://api.test"
OWN = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
THREADS = "2f3e4d5c-6b7a-4c8d-9e0f-1a2b3c4d5e6f"
THREAD = "01a1e538-9a43-7482-84e9-558d3e7d8ce3"
TOKEN = {"token": "bk_st_thread", "expires_at": "2099-01-01T00:00:00+00:00"}


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person")
    for name in ("BEAKR_SESSION", "CLAUDE_CODE_SESSION_ID", "CURSOR_CONVERSATION_ID"):
        monkeypatch.delenv(name, raising=False)
    # Set, then removed: the test's own change to CODEX_THREAD_ID is undone after it.
    monkeypatch.setenv("CODEX_THREAD_ID", "placeholder")
    monkeypatch.delenv("CODEX_THREAD_ID")
    monkeypatch.setattr(scope_session, "remembered_selection", lambda folder: None)


def _meta(**turn) -> SimpleNamespace:
    """A request's ``_meta`` as the MCP library hands it over: extra keys kept."""
    return SimpleNamespace(
        model_extra={
            "x-codex-turn-metadata": {"thread_id": THREAD, "session_id": THREAD, **turn},
            "threadId": THREAD,
        }
    )


def _own_session(monkeypatch) -> McpSession:
    held = McpSession(folder=None, agent=None)
    held.state = TokenState(OWN, "bk_st_own", "2099-01-01T00:00:00+00:00")
    held.session = {"id": OWN, "status": "active", "mode": "automatic"}
    monkeypatch.setattr(scope_session, "ACTIVE", held)
    return held


def test_the_thread_id_is_read_from_codexs_turn_metadata():
    assert scope_session.codex_thread_id(_meta()) == THREAD
    assert scope_session.codex_thread_id(SimpleNamespace(model_extra={"threadId": THREAD})) == (
        THREAD
    )
    assert scope_session.codex_thread_id(SimpleNamespace(model_extra={})) is None
    assert scope_session.codex_thread_id(None) is None
    # Not something the API would take as a conversation id: ignored.
    bad = SimpleNamespace(model_extra={"x-codex-turn-metadata": {"thread_id": "a b/c"}})
    assert scope_session.codex_thread_id(bad) is None


@respx.mock
def test_before_its_first_call_the_server_joins_the_threads_session(monkeypatch):
    import os

    held = _own_session(monkeypatch)
    ended = respx.post(f"{API}/v1/sessions/{OWN}/end").respond(json={})
    joined = respx.post(f"{API}/v1/sessions").respond(
        201,
        json={
            "id": THREADS,
            "status": "active",
            "mode": "automatic",
            "token": TOKEN,
            "agent_session": THREAD,
        },
    )

    asyncio.run(scope_session.adopt_conversation(THREAD))
    asyncio.run(held.ensure_started())

    assert ended.called
    assert json.loads(joined.calls.last.request.content)["agent_session"] == THREAD
    assert held.agent == THREAD and held.id == THREADS
    # The shell commands' variable: the X-Beakr-Session header names the thread too.
    assert os.environ["CODEX_THREAD_ID"] == THREAD


@respx.mock
def test_after_a_call_has_run_the_server_keeps_its_session(monkeypatch):
    import os

    held = _own_session(monkeypatch)
    held.calls = 1
    ended = respx.post(f"{API}/v1/sessions/{OWN}/end")

    asyncio.run(scope_session.adopt_conversation(THREAD))

    assert not ended.called
    assert held.agent is None and held.id == OWN
    assert "CODEX_THREAD_ID" not in os.environ


@respx.mock
def test_a_conversation_id_already_in_the_environment_is_kept(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-conv")
    held = _own_session(monkeypatch)
    ended = respx.post(f"{API}/v1/sessions/{OWN}/end")

    asyncio.run(scope_session.adopt_conversation(THREAD))

    assert not ended.called and held.agent is None


def test_a_tool_call_with_codex_metadata_adopts_the_thread(monkeypatch):
    from beakr_cli import mcp_session
    from beakr_cli.mcp_server import mcp

    seen = []

    async def adopt(thread):
        seen.append(thread)

    async def no_gate(server, name, agent):
        return None

    async def call(name, arguments):
        return "ok"

    monkeypatch.setattr(scope_session, "adopt_conversation", adopt)
    monkeypatch.setattr(mcp_session, "gate", no_gate)
    monkeypatch.setattr(mcp, "_request_meta", lambda: _meta())
    monkeypatch.setattr(mcp, "_call_tool", call)

    assert asyncio.run(mcp._gated_call("knowledge_base", {}, None)) == "ok"
    assert seen == [THREAD]
