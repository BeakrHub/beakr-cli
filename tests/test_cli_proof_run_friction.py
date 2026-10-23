"""CLI friction from the 2026-10-13 proof run.

* two flag names for key sign-in (``--api-key`` in messages, ``--with-api-key``
  in the quickstart): ``--with-api-key`` is the documented one, the other stays
  a hidden alias (tests/test_cli_auth_login.py);
* no ``beakr --version``;
* no ``beakr kb sections`` (the MCP's first command); ``kb search`` showed a
  summary instead of the passage that matched (it read ``snippet``, but a hit
  carries ``content_snippet``); ``kb grep`` ran the semantic page search and
  showed no matching line;
* ``-P`` and ``-p`` both meant project depending on the command;
* ``upload --wait`` returned before Knowledge pages existed without saying so.
"""

from __future__ import annotations

import json

import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
PROJECT = "e56b7c9b-4616-4c89-94bc-6c1c49af35cc"
runner = CliRunner()


def _env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def test_version_flag(monkeypatch):
    from beakr_cli import __version__

    for flag in ("--version", "-V"):
        result = runner.invoke(app, [flag])
        assert result.exit_code == 0
        assert result.stdout.strip() == f"beakr-cli {__version__}"


_DOOR = {
    "content": [{"type": "text", "text": "1. [[Lease]] › Facts {{wiki:p1-s1}}\n   TTL 300s"}],
    "sources": [
        {
            "citation_key": "artifact:v1-u2",
            "filename": "runtime.md",
            "provider": "upload",
            "location": {"file_id": "f1"},
            "evidence": {"excerpt": "The lease TTL is 300 seconds."},
        }
    ],
    "outcome": {"status": "success"},
    "next": ["cat a hit with its section_id."],
}


@respx.mock
def test_kb_sections_runs_the_door_and_shows_each_passage(monkeypatch):
    _env(monkeypatch)
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(json=_DOOR)
    result = runner.invoke(app, ["kb", "sections", "lease ttl", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert json.loads(door.calls[0].request.content) == {
        "command": "sections",
        "query": "lease ttl",
        "scope": PROJECT,
    }
    assert "TTL 300s" in result.stdout
    assert "- runtime.md · read_file f1" in result.stdout
    assert '"The lease TTL is 300 seconds."' in result.stdout
    assert "next: cat a hit with its section_id." in result.stdout


@respx.mock
def test_kb_grep_is_the_literal_door_grep(monkeypatch):
    _env(monkeypatch)
    lines = {**_DOOR, "content": [{"type": "text", "text": "Lease  §sec_facts:4  TTL 300s"}]}
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(json=lines)
    result = runner.invoke(app, ["kb", "grep", "TTL", "--regex", "-C", "2", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    sent = json.loads(door.calls[0].request.content)
    assert sent["command"] == "grep" and sent["mode"] == "regex" and sent["context_lines"] == 2
    assert "§sec_facts:4  TTL 300s" in result.stdout


@respx.mock
def test_kb_search_shows_the_passage_that_matched(monkeypatch):
    _env(monkeypatch)
    hit = {
        "id": "p1",
        "title": "Lease",
        "page_type": "protocol",
        "summary": "A protocol page.",
        "content_snippet": "The coordinator lease TTL is 300 seconds.",
    }
    respx.get(f"{API}/v1/knowledge/wiki/search").respond(json={"data": [hit], "has_more": False})
    piped = runner.invoke(app, ["kb", "search", "lease", "-P", PROJECT])
    assert piped.stdout.split("\t")[-1].strip() == "The coordinator lease TTL is 300 seconds."
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    shown = runner.invoke(app, ["kb", "search", "lease", "-P", PROJECT])
    assert "The coordinator lease TTL is 300 seconds." in shown.stdout


def test_project_is_capital_p_everywhere_and_lowercase_p_still_works(monkeypatch):
    help_text = runner.invoke(app, ["auth", "set-scope", "--help"], env={"COLUMNS": "200"})
    assert "-P" in help_text.stdout and " -p " not in help_text.stdout
    result = runner.invoke(app, ["auth", "set-scope", "-p", PROJECT, "--json"])
    assert result.exit_code == 0, result.output
    from beakr_cli import config

    assert config.get("project_id") == PROJECT


@respx.mock
def test_upload_wait_says_knowledge_compiles_afterwards(monkeypatch, tmp_path):
    _env(monkeypatch)
    mock_resumable_uploads(API)
    respx.get(url__regex=rf"{API}/v1/jobs/.+").respond(
        json={"job_id": "j", "kind": "artifact_index", "status": "succeeded"}
    )
    path = tmp_path / "notes.md"
    path.write_text("hello")
    piped = runner.invoke(app, ["upload", str(path), "-P", PROJECT, "--wait"])
    assert piped.exit_code == 0, piped.output
    assert piped.stdout.splitlines()[-1].startswith(f"next: beakr kb ls -P {PROJECT}")
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    shown = runner.invoke(app, ["upload", str(path), "-P", PROJECT, "--wait"])
    assert "Knowledge pages compile in the background" in " ".join(shown.stdout.split())


def test_upload_help_says_how_folder_paths_are_kept():
    text = " ".join(runner.invoke(app, ["upload", "--help"], env={"COLUMNS": "200"}).stdout.split())
    # The folder's own name IS added (the SDK's upload_folder defaults
    # root_folder=True); the help used to say it was not.
    assert "A folder's own name becomes the top folder" in text and "--no-mirror" in text
    assert "its own name is not added" not in text
