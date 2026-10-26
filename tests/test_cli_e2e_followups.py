"""Behaviors an end-to-end run against a live server found wrong (2026-10-25).

A fresh install drove every command against sandbox as an outside developer
would. Each test here names the root cause it pins.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "11111111-2222-3333-4444-555555555555"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _page(title: str, page_type: str = "person") -> dict:
    return {"id": f"id-{title}", "title": title, "page_type": page_type, "updated_at": "2026-10-25"}


# Root cause: with no project the command called the pages route without `all`,
# which answers only organization-root pages, so anyone whose Knowledge lives in
# projects got an empty list.
@respx.mock
def test_kb_entities_without_a_project_lists_every_scope():
    route = respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        json={"data": [_page("Ada")], "has_more": False, "next_cursor": None}
    )
    result = runner.invoke(app, ["kb", "entities", "--json"])
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params.get("all") == "true"
    assert "project_id" not in route.calls.last.request.url.params
    assert [p["title"] for p in json.loads(result.stdout)] == ["Ada"]


# Root cause: the command ignored BEAKR_PROJECT_ID and the directory binding that
# every other kb read honours.
@respx.mock
def test_kb_entities_uses_the_pinned_project(monkeypatch):
    monkeypatch.setenv("BEAKR_PROJECT_ID", PROJECT)
    route = respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        json={"data": [], "has_more": False, "next_cursor": None}
    )
    result = runner.invoke(app, ["kb", "entities", "--json"])
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params.get("project_id") == PROJECT
    assert "all" not in route.calls.last.request.url.params


# Root cause: the compact summary had no id key for a token (`token_id`) and took
# `client_name`, shared by every token one backend mints, as its label, so an
# agent could not pick a token to revoke without --json.
@respx.mock
def test_end_user_tokens_ls_piped_shows_token_id_and_subject():
    respx.get(f"{API}/v1/auth/end-user-tokens").respond(
        json=[
            {
                "token_id": "tok-1",
                "subject": "acme-user-42",
                "client_name": "my-backend",
                "expires_at": "2026-10-26T00:00:00Z",
            }
        ]
    )
    result = runner.invoke(app, ["end-user-tokens", "ls"])
    assert result.exit_code == 0, result.output
    first = result.stdout.splitlines()[0].split("\t")
    assert first[0] == "tok-1"
    assert first[1] == "acme-user-42"


# Root cause: an integration's `label` and `provider` were not summary keys, so a
# piped listing showed an id and nothing that said which integration it was.
@respx.mock
def test_integrations_ls_piped_shows_label_and_provider():
    respx.get(f"{API}/v1/integrations").respond(
        json={
            "data": [
                {
                    "id": "c1",
                    "provider": "gdrive:nango",
                    "label": "Work drive",
                    "scope": "user",
                    "health_status": "healthy",
                }
            ]
        }
    )
    result = runner.invoke(app, ["integrations", "ls"])
    assert result.exit_code == 0, result.output
    line = result.stdout.splitlines()[0]
    assert line.startswith("c1\tWork drive\t")
    assert "provider=gdrive:nango" in line


# Root cause: a tool has no id field (its name is what `integrations call`
# takes), so the generic summary led each line with "-".
@respx.mock
def test_integrations_tools_piped_leads_with_the_tool_name():
    respx.get(f"{API}/v1/integrations/tools").respond(
        json=[
            {
                "name": "slack_search",
                "provider": "slack",
                "connections": [{"id": "c1"}],
                "description": "Search Slack messages.",
            }
        ]
    )
    result = runner.invoke(app, ["integrations", "tools"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].split("\t")[:3] == [
        "slack_search",
        "provider=slack",
        "connections=1",
    ]


# Root cause: write proposals demanded --project or --mine even inside a bound
# directory, unlike every kb read there.
@respx.mock
def test_kb_propose_create_uses_the_pinned_project(monkeypatch):
    monkeypatch.setenv("BEAKR_PROJECT_ID", PROJECT)
    route = respx.post(f"{API}/v1/knowledge/wiki/proposals").respond(
        json={"id": "prop-1", "status": "pending", "proposal_type": "create"}
    )
    result = runner.invoke(
        app,
        [
            "kb",
            "propose",
            "create",
            "--title",
            "Plate reader",
            "--content",
            "Controls run first.",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content)["project_id"] == PROJECT


def test_kb_propose_create_still_needs_a_project_when_nothing_is_pinned():
    result = runner.invoke(
        app, ["kb", "propose", "create", "--title", "Plate reader", "--content", "x"]
    )
    assert result.exit_code == 1
    assert "bound with `beakr init`" in result.output


# Root cause: documents search always searched everything, ignoring the pinned
# project the kb commands in the same directory used.
@respx.mock
def test_documents_search_uses_the_pinned_project_unless_all(monkeypatch):
    monkeypatch.setenv("BEAKR_PROJECT_ID", PROJECT)
    route = respx.post(f"{API}/v1/knowledge/documents").respond(
        json={"content": "documents search: 0 matching documents", "sources": {}}
    )
    pinned = runner.invoke(app, ["documents", "search", "calibration", "--json"])
    assert pinned.exit_code == 0, pinned.output
    assert json.loads(route.calls.last.request.content).get("project_id") == PROJECT

    everything = runner.invoke(app, ["documents", "search", "calibration", "--all", "--json"])
    assert everything.exit_code == 0, everything.output
    assert "project_id" not in json.loads(route.calls.last.request.content)


def test_documents_search_refuses_project_and_all_together():
    result = runner.invoke(app, ["documents", "search", "x", "-P", PROJECT, "--all"])
    assert result.exit_code == 2
