"""Independent CLI acceptance for full project lists and organization claim routing."""

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_projects_ls_keeps_member_filter_across_all_pages():
    route = respx.get(f"{API}/v1/projects").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [{"id": "p1", "name": "First"}],
                    "next_cursor": "next",
                    "has_more": True,
                },
            ),
            httpx.Response(
                200,
                json={
                    "data": [{"id": "p2", "name": "Second"}],
                    "next_cursor": None,
                    "has_more": False,
                },
            ),
        ]
    )
    result = CliRunner().invoke(app, ["projects", "ls", "--member-only", "--json"])
    assert result.exit_code == 0, result.output
    assert [r["id"] for r in json.loads(result.stdout)] == ["p1", "p2"]
    assert len(route.calls) == 2
    assert all(call.request.url.params["member_only"] == "true" for call in route.calls)
    assert route.calls[1].request.url.params["cursor"] == "next"
