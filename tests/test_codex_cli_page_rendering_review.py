"""Named commands must render the page envelopes in the public contract."""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    ("command", "path", "row", "visible"),
    [
        (
            ["runs", "ls"],
            "/v1/me/threads",
            {"thread_id": "t1", "title": "Calibration review"},
            "Calibration review",
        ),
        (
            ["outputs", "history", "c1"],
            "/v1/outputs/c1/revisions",
            {"revision_no": 3, "actor_name": "Ada", "summary": "Tightened intro"},
            "Tightened intro",
        ),
    ],
)
@respx.mock
def test_named_commands_render_contract_page_rows(
    monkeypatch, command, path, row, visible, json_output
):
    # ThreadListResponse and RevisionOutPage both put rows under data. Legacy
    # mocks using threads/revisions or bare lists hid empty terminal output.
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    page = {"data": [row], "next_cursor": None, "has_more": False}
    respx.get(f"https://api.test{path}").respond(200, json=page)
    result = CliRunner().invoke(app, command + (["--json"] if json_output else []))
    assert result.exit_code == 0, result.output
    if json_output:
        assert json.loads(result.output) == page
    else:
        assert visible in result.output
