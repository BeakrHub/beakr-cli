"""Tools used and Beakr reads extracted from transcripts (PROSE-392, PROSE-409).

Regression: Claude Code and Codex captures never sent ``sources_read``, so the
engine could never check their freshness ("unknown" for every session), and no
import recorded which tools a session used.
"""

from __future__ import annotations

import json
from pathlib import Path

from beakr_cli.transcript_activity import SessionActivity
from beakr_cli.transcripts import parse_claude_code_transcript, parse_codex_session

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
HELIOS = "ec1c3f22-686a-4d48-ad46-a5a954ebb8b4"
DANA = "28866dc7-330e-481e-85c0-0c2552901ee4"
MAJA = "9f444d1c-3dd0-44c2-87c1-ba19b83f3ffa"
CHARTER = "8f6f9e00-7e50-4199-8443-dc7977d167d6"
CHARTER_V1 = "b84f68a2-d917-4b6b-ba38-073f623c7c81"
MEMO = "bb4de0e9-1f4e-4c2e-88aa-efcd4352de0c"


def test_claude_code_reads_are_extracted_with_their_versions():
    session = parse_claude_code_transcript(FIXTURES / "claude_code_beakr_reads.jsonl")
    assert session.sources_read == [
        # knowledge_base cat: id and revision from the page header, not from a
        # "| Rev: n |" further down the page.
        {"page_id": HELIOS, "revision": 3},
        # show {page, rev} under another server name prints no id.
        {"page_id": DANA, "revision": 2},
        # read_file: the version its structured result reports.
        {"document_id": CHARTER, "version_id": CHARTER_V1},
        # `beakr kb cat` in a shell prints the page JSON.
        {"page_id": MAJA, "revision": 5},
        # `beakr files download` names the document but not its version.
        {"document_id": MEMO},
    ]


def test_failed_calls_searches_and_other_servers_record_no_read():
    session = parse_claude_code_transcript(FIXTURES / "claude_code_beakr_reads.jsonl")
    pages = {r.get("page_id") for r in session.sources_read}
    # A failed cat, a grep hit and another server's read_file are not reads.
    assert len(session.sources_read) == 5
    assert pages >= {HELIOS, DANA, MAJA}


def test_claude_code_tools_are_counted():
    session = parse_claude_code_transcript(FIXTURES / "claude_code_beakr_reads.jsonl")
    assert session.tools_used == [
        {"name": "mcp__beakr__knowledge_base", "count": 3},
        {"name": "Bash", "count": 2},
        {"name": "mcp__beakr-local__knowledge_base", "count": 1},
        {"name": "mcp__beakr__read_file", "count": 1},
        {"name": "mcp__filesystem__read_file", "count": 1},
    ]
    # Tool traffic still never becomes a message.
    assert [m["role"] for m in session.messages] == ["user", "assistant", "assistant"]


def test_codex_reads_and_tools_are_extracted_once_per_call():
    session = parse_codex_session(FIXTURES / "codex_beakr_reads.jsonl")
    assert session.sources_read == [
        {"page_id": HELIOS, "revision": 3},
        {"document_id": CHARTER, "version_id": CHARTER_V1},
        {"page_id": MAJA, "revision": 5},
    ]
    # The MCP call's function_call and its mcp_tool_call_end event are one call.
    assert session.tools_used == [
        {"name": "beakr__knowledge_base", "count": 1},
        {"name": "beakr__read_file", "count": 1},
        {"name": "local_shell", "count": 1},
        {"name": "shell", "count": 1},
    ]


def test_a_historical_cat_reads_the_revision_it_shows():
    activity = SessionActivity()
    activity.call("t", "mcp__beakr__knowledge_base", {"command": "cat", "page": "Helios"})
    activity.result("t", f"# Project Helios (revision 2)\nKB page ID: {HELIOS}\n\nBody.")
    assert activity.sources_read() == [{"page_id": HELIOS, "revision": 2}]


def test_a_cat_without_a_revision_is_an_unversioned_read():
    activity = SessionActivity()
    activity.call("t", "knowledge_base", json.dumps({"command": "cat", "page": "x"}))
    activity.result("t", {"content": f"# X\nKB page ID: {HELIOS}\n\nBody."})
    assert activity.sources_read() == [{"page_id": HELIOS}]


def test_an_unsuccessful_structured_result_records_nothing():
    activity = SessionActivity()
    activity.call("t", "mcp__beakr__knowledge_base", {"command": "cat", "page": "x"})
    activity.result("t", json.dumps({"status": "error", "content": f"KB page ID: {HELIOS}"}))
    assert activity.sources_read() == []


def test_a_result_without_its_call_is_ignored():
    activity = SessionActivity()
    activity.result("never-called", f"KB page ID: {HELIOS} | Rev: 1 |")
    assert activity.sources_read() == [] and activity.tools_used() == []
