"""ChatGPT data exports: parsing and ``beakr conversations import --client chatgpt-export``."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.chatgpt_export import ChatGPTExportError, parse_chatgpt_export
from beakr_cli.main import app
from beakr_cli.transcripts import UNKNOWN, parse_transcript

API = "https://api.test"
FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
EXPORT = FIXTURES / "chatgpt_export" / "conversations.json"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def test_an_export_reads_the_branch_last_seen_and_skips_empty_conversations():
    first, second = parse_chatgpt_export(EXPORT)
    assert first.session_id == "chatgpt-conv-1" and second.session_id == "chatgpt-conv-3"
    assert first.title == "Helios budget questions"
    # The edited-away draft, hidden custom instructions, the code sent to the
    # Python tool and its output are not messages; the image part is dropped.
    assert [(m["role"], m["content"]) for m in first.messages] == [
        ("user", "Plot the Helios quarterly spend."),
        ("assistant", "Here is the plot of quarterly spend."),
        ("user", "What does this chart show?"),
        ("assistant", "Spend rises each quarter."),
    ]
    assert first.messages[0]["at"] == "2024-10-10T08:48:20+00:00"
    assert first.tools_used == [{"name": "python", "count": 1}]


def test_exported_turns_are_never_attributed_to_a_person():
    """An export carries no typed-by-a-person marker, so learning never quotes it."""
    for session in parse_chatgpt_export(EXPORT):
        assert session.classification == UNKNOWN
        assert {m["author"] for m in session.messages} <= {"unknown", "agent"}


def test_the_export_zip_is_read_directly(tmp_path: Path):
    archive = tmp_path / "chatgpt-export.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(EXPORT, "conversations.json")
        handle.writestr("chat.html", "<html></html>")
    assert [s.session_id for s in parse_chatgpt_export(archive)] == [
        "chatgpt-conv-1",
        "chatgpt-conv-3",
    ]


def test_a_file_that_is_not_an_export_is_refused(tmp_path: Path):
    bad = tmp_path / "conversations.json"
    bad.write_text('"just a string"')
    with pytest.raises(ChatGPTExportError):
        parse_chatgpt_export(bad)


def test_parse_transcript_refuses_a_multi_conversation_export():
    with pytest.raises(ValueError, match="holds 2 conversations"):
        parse_transcript(EXPORT, "chatgpt-export")


@respx.mock
def test_cli_imports_every_conversation_with_a_summary_and_its_tools():
    route = respx.post(f"{API}/v1/conversations/import").mock(
        side_effect=[
            httpx.Response(200, json={"conversation_id": c, "created": True, "stored": "summary"})
            for c in ("c1", "c3")
        ]
    )
    result = CliRunner().invoke(
        app,
        ["conversations", "import", "--client", "chatgpt-export", "--from", str(EXPORT), "--json"],
    )
    assert result.exit_code == 0, result.output
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert [b["client"] for b in bodies] == ["chatgpt-export", "chatgpt-export"]
    assert [b["session_id"] for b in bodies] == ["chatgpt-conv-1", "chatgpt-conv-3"]
    assert bodies[0]["tools_used"] == [{"name": "python", "count": 1}]
    assert all(b["summary"].endswith("(Client-side summary by beakr-cli.)") for b in bodies)
    # Full text follows the person's setting unless the command says otherwise.
    assert all("store_full_text" not in b for b in bodies)
    assert [r["conversation_id"] for r in json.loads(result.stdout)] == ["c1", "c3"]


@respx.mock
def test_cli_full_text_flag_overrides_the_setting_for_an_export():
    route = respx.post(f"{API}/v1/conversations/import").mock(
        return_value=httpx.Response(
            200, json={"conversation_id": "c", "created": True, "stored": "summary"}
        )
    )
    result = CliRunner().invoke(
        app,
        ["conversations", "import", "--client", "chatgpt-export", "--from", str(EXPORT),
         "--no-full-text", "--json"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert all(json.loads(c.request.content)["store_full_text"] is False for c in route.calls)


def test_cli_refuses_one_summary_for_a_whole_export():
    result = CliRunner().invoke(
        app,
        ["conversations", "import", "--client", "chatgpt-export", "--from", str(EXPORT),
         "--summary", "x"],
    )  # fmt: skip
    assert result.exit_code == 2
