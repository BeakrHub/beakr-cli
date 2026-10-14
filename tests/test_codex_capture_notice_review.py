"""SPEC-480: the refreshed Codex context must disclose local capture consent."""

import pytest
import respx

from beakr_cli import agent_hooks
from beakr_cli.commands.codex_wrapper import refresh_codex_context
from beakr_cli.project_binding import capture_enabled, write_binding


@pytest.mark.parametrize("capture", [False, True])
@respx.mock
def test_refreshed_codex_context_discloses_capture_state(tmp_path, monkeypatch, capture):
    api = "https://api.test"
    monkeypatch.setenv("BEAKR_API_URL", api)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_test_notice")
    write_binding(
        tmp_path,
        {"project_id": "p1", "base_url": api, "org_id": None, "capture": capture},
    )
    monkeypatch.chdir(tmp_path)
    assert capture_enabled(tmp_path) is capture
    request = respx.post(f"{api}/v1/context").respond(
        json={"content": "# Project\nFacts from the engine."}
    )

    assert refresh_codex_context(tmp_path)
    assert request.called
    context = agent_hooks.codex_agents_path().read_text()
    assert "Facts from the engine." in context
    expected = "capture is on" if capture else "capture is off"
    assert expected in context.lower(), "Codex's context must say whether it will be captured"
