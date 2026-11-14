"""A ``beakr kb`` read the door refuses exits with the refusal's own code.

Root cause: ``commands/kb._door_print`` ended every error outcome with exit 1, so
``beakr kb log --kind robot`` (the door's ``invalid_arguments``) exited 1, not the
usage error (2) ``errors.exit_code_for`` gives a checked call, and a missing page
exited 1, not 4 (fdoctant attribution run, 14 Nov). The message was already right.
"""

from __future__ import annotations

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "22222222-2222-2222-2222-222222222222"
COMMAND = f"{API}/v1/knowledge/wiki/command"

runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _refused(code: str, text: str) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "outcome": {"status": "error", "error_code": code},
    }


@respx.mock
@pytest.mark.parametrize(
    ("argv", "code", "text", "exit_code"),
    [
        (
            ["kb", "log", "-P", PROJECT, "--kind", "robot"],
            "invalid_arguments",
            "log: kind: 'robot' is not a kind. Use person, agent, beakr or system.",
            2,
        ),
        (
            ["kb", "log", "-P", PROJECT, "--since", "yesterday"],
            "invalid_arguments",
            "log: since: 'yesterday' is not a date. Use YYYY-MM-DD.",
            2,
        ),
        (
            ["kb", "sections", "assay", "-P", PROJECT],
            "page_not_found",
            "No page 'Assay' in this project.",
            4,
        ),
        (
            ["kb", "sections", "assay", "-P", PROJECT],
            "command_failed",
            "The command could not run.",
            1,
        ),
    ],
)
def test_a_refused_read_exits_with_its_codes_exit(argv, code, text, exit_code):
    respx.post(COMMAND).respond(json=_refused(code, text))

    result = runner.invoke(app, argv)

    assert result.exit_code == exit_code, result.output
    # The door's words still reach the terminal.
    assert text.split(":")[0] in result.stdout
