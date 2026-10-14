"""Exercise real CLI payloads against the engine's current text policy, without a DB.

Runs the engine's own code in its ``.venv`` (``tests._engine.real_engine``):
skipped in this repository's CI, which has only the engine snapshot, and run by
the engine's CI.
"""

import json
import subprocess
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from tests._engine import real_engine

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "tests/fixtures/transcripts/claude_code_session.jsonl"
POLICY = """
import json, sys
from datetime import datetime, UTC
from beakr.db.models.developer_platform import ImportedConversation
from beakr.services.conversations.importing import apply_text_policy
body = json.load(sys.stdin)
row = ImportedConversation(
    messages=body['messages'], store_full_text=body.get('store_full_text', False),
    compound_status='none',
)
# Mirror the real route's mapping of capture and deferred-hold intent.
apply_text_policy(
    row, compound=body.get('compound', False) or body.get('hold_text', False),
    now=datetime.now(UTC),
)
print(json.dumps({'has_text': bool(row.messages), 'has_deadline': row.text_release_at is not None}))
"""


@pytest.mark.parametrize("should_compound", [True, False])
@respx.mock
def test_cli_import_requests_the_text_lifecycle_its_next_step_needs(monkeypatch, should_compound):
    engine = real_engine("beakr/services/conversations/importing.py")
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    imported = respx.post("https://api.test/v1/conversations/import").respond(
        200, json={"conversation_id": "conv-1", "created": True, "stored": "summary"}
    )
    if should_compound:
        respx.post("https://api.test/v1/compound").respond(
            202, json={"status": "nothing_to_compound", "conversation_id": "conv-1"}
        )
    command = ["compound"] if should_compound else ["conversations", "import"]
    result = CliRunner().invoke(app, [*command, "--from-transcript", str(TRANSCRIPT), "--json"])
    assert result.exit_code == 0, result.output
    assert imported.called
    body = json.loads(imported.calls.last.request.content)
    policy = subprocess.run(
        [str(engine / ".venv/bin/python"), "-c", POLICY],
        cwd=engine,
        input=json.dumps(body),
        text=True,
        capture_output=True,
        check=True,
    )
    outcome = json.loads(policy.stdout)
    # Left out, the engine applies the person's own per-client setting (off
    # unless they chose it); the CLI itself never opts in.
    assert body.get("store_full_text") in (None, False), "Must not silently opt into full storage"
    assert outcome["has_text"] is should_compound, (body.keys(), outcome, result.stdout)
    assert outcome["has_deadline"] is should_compound
    if should_compound:
        # The mocked explicit compound request found nothing. The CLI must not
        # turn that response into a successful capture report.
        assert json.loads(result.stdout)["compounded"] is False
