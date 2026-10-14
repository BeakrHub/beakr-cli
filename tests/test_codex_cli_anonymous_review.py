"""Contract-anonymous commands must work before a user has signed in."""

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app


@pytest.mark.parametrize("has_key", [False, True])
def test_generated_anonymous_operation_sends_no_credential(monkeypatch, has_key):
    # The public contract document is anonymous in the documented core.
    monkeypatch.setenv("BEAKR_BASE_URL", "https://api.test")
    if has_key:
        monkeypatch.setenv("BEAKR_API_KEY", "bk_test_fixture")
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://api.test/v1/public/openapi.json").respond(200, json={})
        result = CliRunner().invoke(app, ["api", "openapi", "public-openapi"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "Authorization" not in route.calls[0].request.headers
