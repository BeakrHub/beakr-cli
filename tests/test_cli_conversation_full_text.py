"""``beakr conversations full-text`` and what capture sends about full text (PROSE-392)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"

pytestmark = pytest.mark.usefixtures("capture_consented")


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_full_text_shows_the_setting():
    respx.get(f"{API}/v1/conversations/settings").respond(
        200, json={"full_text_clients": ["codex"]}
    )
    result = CliRunner().invoke(app, ["conversations", "full-text"])
    assert result.exit_code == 0, result.output
    assert "codex" in result.output


@respx.mock
def test_full_text_sets_and_clears_the_setting():
    route = respx.put(f"{API}/v1/conversations/settings").mock(
        side_effect=lambda request: httpx.Response(200, json=json.loads(request.content))
    )
    runner = CliRunner()
    assert runner.invoke(app, ["conversations", "full-text", "claude-code", "codex"]).exit_code == 0
    assert runner.invoke(app, ["conversations", "full-text", "--none"]).exit_code == 0
    assert [json.loads(c.request.content) for c in route.calls] == [
        {"full_text_clients": ["claude-code", "codex"]},
        {"full_text_clients": []},
    ]


def test_full_text_refuses_an_unknown_client():
    assert CliRunner().invoke(app, ["conversations", "full-text", "cursor"]).exit_code == 2


@respx.mock
def test_a_transcript_import_sends_its_reads_and_tools_and_defers_full_text():
    route = respx.post(f"{API}/v1/conversations/import").respond(
        200, json={"conversation_id": "conv-1", "created": True, "stored": "summary"}
    )
    result = CliRunner().invoke(
        app,
        ["conversations", "import", "--from-transcript",
         str(FIXTURES / "claude_code_beakr_reads.jsonl"), "--json"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert "store_full_text" not in body
    assert {"page_id": "ec1c3f22-686a-4d48-ad46-a5a954ebb8b4", "revision": 3} in body[
        "sources_read"
    ]
    assert body["tools_used"][0] == {"name": "mcp__beakr__knowledge_base", "count": 3}


@respx.mock
def test_a_codex_import_sends_its_reads():
    """Regression for PROSE-409: the Codex import never sent ``sources_read``."""
    route = respx.post(f"{API}/v1/conversations/import").respond(
        200, json={"conversation_id": "conv-2", "created": True, "stored": "summary"}
    )
    result = CliRunner().invoke(
        app,
        ["conversations", "import", "--client", "codex", "--from-transcript",
         str(FIXTURES / "codex_beakr_reads.jsonl"), "--json"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body["sources_read"][0] == {
        "page_id": "ec1c3f22-686a-4d48-ad46-a5a954ebb8b4",
        "revision": 3,
    }


@respx.mock
def test_the_stop_hook_capture_sends_reads_and_tools():
    """Regression for PROSE-409: the Claude Code hook never sent ``sources_read``."""
    route = respx.post(f"{API}/v1/conversations/import").respond(
        200, json={"conversation_id": "conv-3", "created": True, "stored": "summary"}
    )
    respx.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})
    result = CliRunner().invoke(
        app,
        ["compound", "--from-transcript", str(FIXTURES / "claude_code_beakr_reads.jsonl"),
         "--hook"],
        input="",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert len(body["sources_read"]) == 5
    assert "store_full_text" not in body
    assert body["tools_used"]
