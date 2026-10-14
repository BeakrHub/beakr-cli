"""Resolving a trace id must not require scopes for unrelated resource kinds."""

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app


@pytest.mark.parametrize("allowed_kind", ["page", "output"])
@respx.mock
def test_single_id_trace_returns_the_accessible_kind_without_unrelated_scopes(
    monkeypatch, allowed_kind
):
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    item_id = "4c15ce3b-4b11-45f7-9811-1b5b179c6391"
    for kind in ("page", "output", "run"):
        route = respx.get(f"https://api.test/v1/trace/{kind}/{item_id}")
        if kind == allowed_kind:
            route.respond(200, json={"kind": kind, "id": item_id, "title": "Allowed trace"})
        elif kind == "page":
            route.respond(404, json={"detail": "not found"})
        else:
            scope = "outputs:read" if kind == "output" else "agent:run"
            route.respond(
                403,
                json={"error": {
                    "code": "insufficient_scope", "message": f"Tracing needs {scope}",
                    "required_scope": scope,
                }},
            )

    result = CliRunner().invoke(app, ["trace", item_id, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["kind"] == allowed_kind
