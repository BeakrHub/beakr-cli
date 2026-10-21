"""``beakr kb propose ... --accept --yes`` accepts in JSON and piped output.

Root cause of the regression this guards: every propose command printed the
proposal and returned when output was JSON or piped, before the accept step, so
a CI job running ``--accept --yes`` left the proposal pending and exited 0.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "22222222-2222-2222-2222-222222222222"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _routes():
    created = respx.post(f"{API}/v1/knowledge/wiki/proposals").respond(
        json={
            "id": "prop-1",
            "status": "pending",
            "proposal_type": "create",
            "content_hash": "ab" * 32,
        }
    )
    accepted = respx.post(f"{API}/v1/knowledge/wiki/proposals/prop-1/accept").respond(
        json={"proposal_id": "prop-1", "status": "accepted"}
    )
    return created, accepted


def _create(*extra: str):
    return runner.invoke(
        app,
        [
            "kb",
            "propose",
            "create",
            "--title",
            "T",
            "--content",
            "Body",
            "--project",
            PROJECT,
            *extra,
        ],
    )


@respx.mock
@pytest.mark.parametrize("mode", [[], ["--json"]])
def test_accept_yes_accepts_when_output_is_not_a_terminal(mode):
    created, accepted = _routes()
    result = _create("--accept", "--yes", *mode)
    assert result.exit_code == 0, result.output
    assert created.called and accepted.called
    # Bound to the proposal it just showed: that read's content_hash is echoed.
    assert json.loads(accepted.calls.last.request.content) == {"reviewed_hash": "ab" * 32}
    if mode:
        assert json.loads(result.stdout)["accepted"] is True
    else:  # piped: the compact line says so
        assert "accepted=true" in result.stdout.split()


@respx.mock
@pytest.mark.parametrize("mode", [[], ["--json"]])
def test_an_accepted_proposal_is_reported_with_its_status_after_the_accept(mode):
    # Root cause: the output was the create's proposal with accepted=true added,
    # so it still said status=pending.
    _routes()
    result = _create("--accept", "--yes", *mode)
    assert result.exit_code == 0, result.output
    if mode:
        assert json.loads(result.stdout)["status"] == "accepted"
    else:
        assert "status=accepted" in result.stdout.split()
        assert "status=pending" not in result.stdout.split()


@respx.mock
def test_accept_without_yes_is_refused_not_ignored_when_not_interactive():
    _, accepted = _routes()
    result = _create("--accept", "--json")
    assert result.exit_code == 1
    assert not accepted.called
    assert "--accept needs --yes" in result.output


@respx.mock
def test_without_accept_the_proposal_stays_pending():
    _, accepted = _routes()
    result = _create("--json")
    assert result.exit_code == 0, result.output
    assert not accepted.called
    assert json.loads(result.stdout)["id"] == "prop-1"
