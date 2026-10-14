"""Each message says who wrote it; only a turn the host marks as typed is the person's.

A session a person drives still holds user-role records the host wrote itself:
background task reports (``promptSource: system``, ``origin.kind:
task-notification``), compaction summaries, Codex plugin suggestions. Sent as
plain user turns they became quotable as the person's own words in
imported-session learning (Codex C088), so the parsers drop host-written
records and mark every kept message with an ``author``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from beakr_cli.transcripts import (
    HUMAN,
    parse_claude_code_transcript,
    parse_codex_session,
)

TYPED = "Please make the export job retry on timeouts."
REPORT = "I am migrating the billing database to a new ledger schema this month."


def _write(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _claude(kind: str, content: object, **extra: object) -> dict:
    return {
        "type": kind,
        "entrypoint": "cli",
        "sessionId": "mixed",
        "message": {"role": kind, "content": content},
        **extra,
    }


def _typed(text: str) -> dict:
    return _claude("user", text, promptSource="typed", origin={"kind": "human"})


def _authors(messages: list[dict]) -> list[tuple[str, str]]:
    return [(m["role"], m["author"]) for m in messages]


def test_a_typed_turn_is_human_and_the_agent_turn_is_agent(tmp_path):
    session = parse_claude_code_transcript(
        _write(tmp_path / "s.jsonl", [_typed(TYPED), _claude("assistant", "Retrying now.")])
    )
    assert session.classification == HUMAN
    assert _authors(session.messages) == [("user", "human"), ("assistant", "agent")]


@pytest.mark.parametrize(
    "record",
    [
        # A background task's report, as the current host writes it.
        {"promptSource": "system", "origin": {"kind": "task-notification"}},
        # Either marker alone is enough.
        {"promptSource": "system"},
        {"origin": {"kind": "task-notification"}},
        # A marker value this version does not know is not a person either.
        {"promptSource": "queued-by-hook"},
        {"origin": {"kind": "coordinator"}},
        # The summary a compacted session restarts from.
        {"isCompactSummary": True},
        {"isVisibleInTranscriptOnly": True},
    ],
)
def test_host_written_user_records_in_a_human_session_are_dropped(tmp_path, record):
    session = parse_claude_code_transcript(
        _write(
            tmp_path / "s.jsonl",
            [_typed(TYPED), _claude("user", REPORT, **record), _claude("assistant", "Noted.")],
        )
    )
    assert session.classification == HUMAN
    assert [m["content"] for m in session.messages] == [TYPED, "Noted."]
    assert all(REPORT not in m["content"] for m in session.messages)


def test_an_unmarked_task_notification_body_is_dropped(tmp_path):
    # An older build that wrote the report without markers: the body gives it away.
    report = f"<task-notification><result>{REPORT}</result></task-notification>"
    session = parse_claude_code_transcript(
        _write(tmp_path / "s.jsonl", [_typed(TYPED), _claude("user", report)])
    )
    assert [m["content"] for m in session.messages] == [TYPED]


def test_tool_results_are_not_turns(tmp_path):
    tool_result = [{"type": "tool_result", "tool_use_id": "t1", "content": REPORT}]
    session = parse_claude_code_transcript(
        _write(tmp_path / "s.jsonl", [_typed(TYPED), _claude("user", tool_result)])
    )
    assert [m["content"] for m in session.messages] == [TYPED]


def test_an_unmarked_user_record_in_a_human_session_is_unknown_not_human(tmp_path):
    # The session is human (one typed prompt), but this record has no marker of
    # its own: the session's classification does not make it the person's.
    session = parse_claude_code_transcript(
        _write(tmp_path / "s.jsonl", [_typed(TYPED), _claude("user", "An unmarked turn.")])
    )
    assert session.classification == HUMAN
    assert _authors(session.messages) == [("user", "human"), ("user", "unknown")]


def test_a_resumed_session_keeps_authorship_per_record(tmp_path):
    # Typed, then a task report, then typed again after the resume: the two
    # typed turns are human, the report is gone, positions stay in order.
    later = "Now add a test for the timeout retry."
    session = parse_claude_code_transcript(
        _write(
            tmp_path / "s.jsonl",
            [
                _typed(TYPED),
                _claude("assistant", "Done."),
                _claude(
                    "user",
                    REPORT,
                    promptSource="system",
                    origin={"kind": "task-notification"},
                ),
                _typed(later),
                _claude("assistant", "Added."),
            ],
        )
    )
    assert [(m["content"], m["author"]) for m in session.messages] == [
        (TYPED, "human"),
        ("Done.", "agent"),
        (later, "human"),
        ("Added.", "agent"),
    ]


def _codex(source: object, *user_texts: str) -> list[dict]:
    records: list[dict] = [
        {
            "type": "session_meta",
            "payload": {"id": "cx", "originator": "Codex Desktop", "source": source},
        }
    ]
    for text in user_texts:
        records.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
    records.append(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "On it."}],
            },
        }
    )
    return records


def test_codex_drops_host_turns_and_marks_plain_turns_human_in_a_human_session(tmp_path):
    session = parse_codex_session(
        _write(
            tmp_path / "r.jsonl",
            _codex(
                "vscode",
                "<recommended_plugins>a list</recommended_plugins>",
                "<heartbeat>tick</heartbeat>",
                TYPED,
                "<some_new_host_tag>payload</some_new_host_tag>",
            ),
        )
    )
    assert session.classification == HUMAN
    assert [(m["content"], m["author"]) for m in session.messages] == [
        (TYPED, "human"),
        ("<some_new_host_tag>payload</some_new_host_tag>", "unknown"),
        ("On it.", "agent"),
    ]


@pytest.mark.parametrize("source", ["exec", {"subagent": {"thread_spawn": {}}}, None])
def test_codex_user_turns_are_never_human_outside_a_human_session(tmp_path, source):
    session = parse_codex_session(_write(tmp_path / "r.jsonl", _codex(source, TYPED)))
    assert session.classification != HUMAN
    assert [m["author"] for m in session.messages if m["role"] == "user"] == ["unknown"]
