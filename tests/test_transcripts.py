"""Session files become only what the person and the agent said."""

from __future__ import annotations

from pathlib import Path

import pytest

from beakr_cli.transcripts import (
    parse_claude_code_transcript,
    parse_codex_session,
    parse_transcript,
)

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def test_claude_code_keeps_text_and_drops_tools_meta_commands_and_sidechains() -> None:
    session = parse_claude_code_transcript(FIXTURES / "claude_code_session.jsonl")

    assert session.session_id == "cc-session-1"
    assert session.cwd == "/work/repo"
    assert session.messages == [
        {
            "role": "user",
            "content": "Refactor the billing client to retry on 503.",
            # The fixture predates per-record markers: never assumed human.
            "author": "unknown",
            "at": "2026-10-08T10:00:00Z",
        },
        {
            "role": "assistant",
            "content": "I will add a retry loop.",
            "author": "agent",
            "at": "2026-10-08T10:00:05Z",
        },
        {
            "role": "assistant",
            "content": "Done: retries on 503 with backoff.",
            "author": "agent",
            "at": "2026-10-08T10:00:09Z",
        },
    ]
    assert session.title == "Refactor the billing client to retry on 503."


def test_claude_code_files_touched_are_written_files_only() -> None:
    session = parse_claude_code_transcript(FIXTURES / "claude_code_session.jsonl")
    # Read is not a change; Edit and Write are.
    assert session.files_touched == ["/work/repo/billing.py", "/work/repo/tests/test_billing.py"]


def test_codex_rollout_drops_injected_context_and_non_message_items() -> None:
    session = parse_codex_session(FIXTURES / "codex_rollout.jsonl")

    assert session.session_id == "codex-session-1"
    assert session.cwd == "/work/other"
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[0]["content"] == "Why does the export job time out?"
    assert session.title == "Why does the export job time out?"


def test_codex_legacy_format_with_bare_message_lines() -> None:
    session = parse_codex_session(FIXTURES / "codex_legacy.jsonl")
    assert session.session_id == "codex-legacy-1"
    assert [m["content"] for m in session.messages] == [
        "List the flaky tests.",
        "test_export and test_upload.",
    ]


def test_missing_session_id_falls_back_to_file_stem(tmp_path: Path) -> None:
    path = tmp_path / "abc123.jsonl"
    path.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
    assert parse_claude_code_transcript(path).session_id == "abc123"


def test_dispatch_rejects_unknown_client() -> None:
    with pytest.raises(ValueError):
        parse_transcript(FIXTURES / "codex_rollout.jsonl", "chatgpt")
