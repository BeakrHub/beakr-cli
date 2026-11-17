"""`beakr runs ls` inside an agent session says why it is not there, plainly.

Agent runs are the person's own, not the session's projects' content, so the API
refuses listing them inside an agent session (engine #2932: 403
session_route_unavailable, with the reason). Before that it answered with a misleading
key-scope refusal ("restricted to specific projects ... not addressed by a single
project"). The CLI prints the API's reason and where it does work, exit 3.
"""

from __future__ import annotations

import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
REASON = (
    "Your agent runs isn't available inside an agent session: they are yours, not this "
    "session's projects' content. List them as yourself in the Beakr web app, or with the "
    "CLI outside an agent session."
)
runner = CliRunner()


def test_runs_ls_inside_a_session_prints_the_apis_reason(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person")
    with respx.mock:
        route = respx.get(url__regex=rf"{API}/v1/me/threads.*").respond(
            403,
            json={
                "error": {
                    "code": "session_route_unavailable",
                    "message": REASON,
                    "fix": "Run it outside the agent session.",
                }
            },
        )
        result = runner.invoke(app, ["runs", "ls"])

    # GET /v1/me/threads, the alias of /v1/assistant/threads/mine #2932 refuses.
    assert route.calls.last.request.url.path == "/v1/me/threads"
    assert result.exit_code == 3, result.output
    text = " ".join(result.output.split())
    assert REASON in text
    assert "To fix: Run it outside the agent session." in text
    assert "required_scope" not in text and "restricted to specific projects" not in text
