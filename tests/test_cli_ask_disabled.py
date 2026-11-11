"""``beakr ask`` where the deployment turned Ask off (``web_ask_enabled``).

The API refuses to start Beakr's chat agent there (``403 ask_disabled``); the CLI
says so in one line and exits nonzero, never with a traceback.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.errors import ASK_DISABLED_MESSAGE, EXIT_AUTH
from beakr_cli.main import app

API = "https://api.test"

_REFUSAL = {
    "detail": {
        "code": "ask_disabled",
        "message": "Ask is turned off for this deployment.",
        "fix": None,
    },
    "error": {"code": "ask_disabled", "message": "Ask is turned off for this deployment."},
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_ask_says_ask_is_off_in_one_line():
    respx.post(f"{API}/v1/runs").respond(403, json=_REFUSAL)

    result = CliRunner().invoke(app, ["ask", "What changed in Helios?"])

    assert result.exit_code == EXIT_AUTH
    assert result.output.strip() == f"Error: {ASK_DISABLED_MESSAGE}"
    assert "Traceback" not in result.output


@respx.mock
def test_ask_json_names_the_code():
    respx.post(f"{API}/v1/runs").respond(403, json=_REFUSAL)

    result = CliRunner().invoke(app, ["ask", "hi", "--json"])

    assert result.exit_code == EXIT_AUTH
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "ask_disabled"
