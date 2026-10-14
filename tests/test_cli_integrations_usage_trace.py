"""``beakr integrations``, ``beakr usage`` and ``beakr trace`` (mocked engine routes)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def rendered(monkeypatch):
    """Render tables, as in a terminal, instead of JSON for a pipe."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


@respx.mock
def test_integrations_ls_lists_connections():
    respx.get(f"{API}/v1/integrations").respond(
        200,
        json={
            "data": [
                {
                    "id": "c1",
                    "provider": "gdrive:nango",
                    "label": "Work",
                    "scope": "user",
                    "health_status": "healthy",
                    "projects": [{"project_id": "p1"}],
                }
            ]
        },
    )
    result = runner.invoke(app, ["integrations", "ls", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"][0]["id"] == "c1"


@respx.mock
def test_integrations_connect_prints_the_link_without_opening_when_asked(monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    route = respx.post(f"{API}/v1/integrations/slack/connect").respond(
        200, json={"provider": "slack", "connect_url": "https://connect.example/x"}
    )
    result = runner.invoke(app, ["integrations", "connect", "slack", "--org", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "https://connect.example/x" in result.output
    assert opened == []
    assert json.loads(route.calls.last.request.content) == {"scope": "org"}


@respx.mock
def test_integrations_coverage_renders_reasons(rendered):
    respx.get(f"{API}/v1/integrations/c1/coverage").respond(
        200,
        json={
            "known": 10,
            "indexed": 8,
            "compiled": 6,
            "not_readable": 1,
            "removed": 0,
            "not_compiled": {"skipped": {"duplicate_content": 2}},
        },
    )
    result = runner.invoke(app, ["integrations", "coverage", "c1"])
    assert result.exit_code == 0, result.output
    assert "compiled 6" in result.output and "duplicate_content" in result.output


@respx.mock
def test_integrations_sync_passes_the_project():
    route = respx.post(f"{API}/v1/integrations/c1/sync").respond(
        202, json={"results": [{"project_id": "p1", "name": "Assays", "status": "queued"}]}
    )
    result = runner.invoke(app, ["integrations", "sync", "c1", "--project", "p1"])
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["project_id"] == "p1"
    assert "queued" in result.output


@respx.mock
def test_integrations_rm_requires_yes_when_not_interactive():
    route = respx.delete(f"{API}/v1/integrations/c1").respond(200, json={"ok": True})
    refused = runner.invoke(app, ["integrations", "rm", "c1"])
    assert refused.exit_code != 0 and not route.called
    done = runner.invoke(app, ["integrations", "rm", "c1", "--yes"])
    assert done.exit_code == 0 and route.called


@respx.mock
def test_integrations_scope_refusal_maps_to_an_auth_exit_code():
    respx.get(f"{API}/v1/integrations").respond(
        403,
        json={
            "detail": {"code": "insufficient_scope", "message": "lacks integrations:read"},
            "error": {"code": "insufficient_scope", "message": "lacks integrations:read"},
        },
    )
    result = runner.invoke(app, ["integrations", "ls"])
    assert result.exit_code == 3


@respx.mock
def test_usage_groups_and_totals(rendered):
    route = respx.get(f"{API}/v1/usage").respond(
        200,
        json={
            "group_by": "model",
            "from": "2026-09-01T00:00:00+00:00",
            "to": "2026-10-01T00:00:00+00:00",
            "scope": "org",
            "data": [{"model": "claude-x", "calls": 3, "cost_usd": 1.5, "tokens": 900}],
            "total": {"calls": 3, "cost_usd": 1.5, "tokens": 900},
        },
    )
    result = runner.invoke(app, ["usage", "--group-by", "model", "--org", "--from", "2026-09-01"])
    assert result.exit_code == 0, result.output
    assert "claude-x" in result.output and "Total: 3 calls" in result.output
    params = route.calls.last.request.url.params
    assert params["scope"] == "org" and params["from"] == "2026-09-01"


def test_usage_rejects_an_unknown_grouping():
    result = runner.invoke(app, ["usage", "--group-by", "week"])
    assert result.exit_code == 2


@respx.mock
def test_trace_shows_inputs_and_actors(rendered):
    respx.get(f"{API}/v1/trace/page/p1").respond(
        200,
        json={
            "kind": "page",
            "id": "p1",
            "title": "Pump",
            "inputs": [{"source_type": "artifact", "title": "assay.pdf"}],
            "runs": [{"id": "e1", "type": "compile", "status": "completed"}],
            "versions": [{"revision": 1}],
            "actors": [{"user_id": "u1", "roles": ["proposed"]}],
        },
    )
    result = runner.invoke(app, ["trace", "page", "p1"])
    assert result.exit_code == 0, result.output
    assert "assay.pdf" in result.output and "u1: proposed" in result.output


def test_trace_rejects_an_unknown_kind():
    assert runner.invoke(app, ["trace", "thread", "x"]).exit_code == 2


@respx.mock
def test_trace_not_found_maps_to_exit_code_4():
    respx.get(f"{API}/v1/trace/run/r9").mock(
        return_value=httpx.Response(404, json={"detail": "Run not found"})
    )
    assert runner.invoke(app, ["trace", "run", "r9"]).exit_code == 4


# SPEC-360: `beakr trace <id>` finds the kind itself.


@respx.mock
def test_trace_with_only_an_id_finds_its_kind(rendered):
    respx.get(f"{API}/v1/trace/page/x1").respond(404, json={"detail": "not found"})
    respx.get(f"{API}/v1/trace/output/x1").respond(
        200, json={"kind": "output", "id": "x1", "title": "Q3 report", "inputs": []}
    )
    respx.get(f"{API}/v1/trace/run/x1").respond(404, json={"detail": "not found"})
    result = runner.invoke(app, ["trace", "x1"])
    assert result.exit_code == 0, result.output
    assert "Q3 report" in result.output


@respx.mock
def test_trace_with_an_ambiguous_id_names_the_candidates():
    respx.get(f"{API}/v1/trace/page/x2").respond(200, json={"kind": "page", "id": "x2"})
    respx.get(f"{API}/v1/trace/output/x2").respond(404, json={"detail": "not found"})
    respx.get(f"{API}/v1/trace/run/x2").respond(200, json={"kind": "run", "id": "x2"})
    result = runner.invoke(app, ["trace", "x2"])
    assert result.exit_code == 2
    assert "page, run" in result.output


@respx.mock
def test_trace_with_an_unknown_id_is_not_found():
    for kind in ("page", "output", "run"):
        respx.get(f"{API}/v1/trace/{kind}/x3").respond(404, json={"detail": "not found"})
    assert runner.invoke(app, ["trace", "x3"]).exit_code == 4


# C087: a kind the credential may not trace is skipped, not fatal.


def _scope_refusal(scope: str) -> dict:
    return {
        "error": {
            "code": "insufficient_scope",
            "message": f"This needs {scope}",
            "required_scope": scope,
        }
    }


@respx.mock
def test_trace_by_id_refused_for_every_kind_reports_the_scope_not_found():
    respx.get(f"{API}/v1/trace/page/x3").respond(404, json={"detail": "not found"})
    respx.get(f"{API}/v1/trace/output/x3").respond(403, json=_scope_refusal("outputs:read"))
    respx.get(f"{API}/v1/trace/run/x3").respond(403, json=_scope_refusal("agent:run"))
    result = runner.invoke(app, ["trace", "x3"])
    assert result.exit_code == 3, result.output
    assert "outputs:read" in result.output


@respx.mock
def test_trace_by_id_does_not_hide_a_session_requirement():
    respx.get(f"{API}/v1/trace/page/x4").respond(
        403, json={"error": {"code": "session_required", "message": "Sign in in a browser"}}
    )
    respx.get(f"{API}/v1/trace/output/x4").respond(200, json={"kind": "output", "id": "x4"})
    result = runner.invoke(app, ["trace", "x4"])
    assert result.exit_code == 3, result.output


@respx.mock
def test_trace_by_id_does_not_hide_an_authentication_failure():
    respx.get(f"{API}/v1/trace/page/x5").respond(
        401, json={"error": {"code": "unauthorized", "message": "Invalid key"}}
    )
    result = runner.invoke(app, ["trace", "x5"])
    assert result.exit_code == 3, result.output
