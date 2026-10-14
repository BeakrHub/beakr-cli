"""Automatic capture needs project opt-in; a deliberate import remains available."""

import json
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app


@pytest.mark.parametrize("automatic", [True, False])
def test_unbound_project_requires_consent_for_automatic_capture(monkeypatch, tmp_path, automatic):
    api = "https://api.test"
    monkeypatch.setenv("BEAKR_API_URL", api)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_review")
    repo = tmp_path / "unbound-repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    fixture = Path(__file__).parent / "fixtures/transcripts/claude_code_session.jsonl"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(fixture.read_text().replace("/work/repo", str(repo)))
    with respx.mock(assert_all_called=False) as mock:
        imported = mock.post(f"{api}/v1/conversations/import").respond(
            json={"conversation_id": "review-conversation", "created": True, "stored": "summary"}
        )
        compounded = mock.post(f"{api}/v1/compound").respond(
            202, json={"status": "queued", "conversation_id": "review-conversation"}
        )
        if automatic:
            # The same invocation the installed Stop hook passes to its child.
            args = ["compound", "--hook", "--client", "claude-code"]
            stdin = json.dumps({"transcript_path": str(transcript), "cwd": str(repo)})
        else:
            args = ["compound", "--from-transcript", str(transcript), "--client", "claude-code"]
            stdin = ""
        result = CliRunner().invoke(app, args, input=stdin)
        assert result.exit_code == 0, result.output
        if automatic:
            assert not imported.called, (
                "Unbound repository's transcript was uploaded by an automatic hook"
            )
            assert not compounded.called
        else:
            assert imported.called and compounded.called


def test_setup_uninstall_removes_the_capture_hooks_it_installed(monkeypatch):
    from beakr_cli import agent_hooks
    from beakr_cli.commands import install

    monkeypatch.setattr(install, "_claude_cli", lambda: None)
    monkeypatch.setattr(install, "_beakr_command", lambda: "/opt/review/beakr")
    monkeypatch.setattr(agent_hooks, "_beakr_command", lambda: "/opt/review/beakr")
    # Editable checkouts lack wheel force-includes; use the exact bundled source assets.
    monkeypatch.setattr(install, "_assets_root", lambda: Path(__file__).parents[1] / ".claude")
    runner = CliRunner()
    installed = runner.invoke(app, ["setup", "--client", "claude", "--no-auth", "--hooks"])
    assert installed.exit_code == 0, installed.output
    assert set(agent_hooks.claude_hooks_installed()) == {"SessionStart", "Stop", "SessionEnd"}
    removed = runner.invoke(app, ["setup", "--client", "claude", "--uninstall"])
    assert removed.exit_code == 0, removed.output
    assert agent_hooks.claude_hooks_installed() == [], "Uninstalled setup still captures sessions"
