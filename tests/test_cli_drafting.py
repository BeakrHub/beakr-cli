"""``beakr workflows draft`` and ``beakr interviews draft``: Beakr drafts, nothing is saved."""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_workflows_draft_writes_the_drafted_definition(tmp_path, monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    route = respx.post(f"{API}/v1/workflows/ai/generate").respond(
        200,
        json={
            "template": {"name": "Run summary"},
            "confidence": 0.8,
            "suggestions": ["Add a date"],
        },
    )
    out = tmp_path / "draft.json"
    result = runner.invoke(app, ["workflows", "draft", "Summarize each run.", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content) == {"description": "Summarize each run."}
    assert json.loads(out.read_text()) == {"name": "Run summary"}
    assert "Drafted template: Run summary" in result.output


@respx.mock
def test_workflows_draft_refines_a_template_file(tmp_path):
    current = tmp_path / "current.json"
    current.write_text(json.dumps({"name": "Old"}))
    route = respx.post(f"{API}/v1/workflows/ai/refine-template").respond(
        200, json={"template": {"name": "New"}, "confidence": 0.9, "changes_made": ["Renamed"]}
    )
    result = runner.invoke(
        app, ["workflows", "draft", "Rename it.", "--refine", str(current), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content) == {
        "current_template": {"name": "Old"},
        "instruction": "Rename it.",
    }
    assert json.loads(result.output)["template"] == {"name": "New"}


@respx.mock
def test_interviews_draft_sends_the_draft_and_prints_the_proposal(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    route = respx.post(f"{API}/v1/interview/sendables/maker").respond(
        200,
        json={
            "reply": "Here is a first draft.",
            "changes": {"key_questions": ["What changed in March?"]},
            "pending_files": [],
        },
    )
    result = runner.invoke(
        app, ["interviews", "draft", "Ask the team about March.", "--title", "March"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content) == {
        "message": "Ask the team about March.",
        "draft": {"title": "March"},
    }
    assert "What changed in March?" in result.output
