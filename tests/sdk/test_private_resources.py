"""Every resource method sends the method, path, query and body the engine expects.

Shapes for existing routes were taken from the engine's route definitions, not
the contract summary: an org role is a query
parameter, profile claims are confirmed with PATCH {"confirm": true}.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx

from .conftest import BASE


# The resources tested here include unlisted routes, reached through the
# private client (``beakr_cli._private_api.PrivateBeakr``) the CLI uses.
@pytest.fixture
def client(internal_client):
    return internal_client


# (label, call, method, path, expected query, expected json body or None)

# Whole-list methods page through the list at the largest page size.
PAGE = {"limit": "200"}

CASES: list[tuple[str, Any, str, str, dict[str, str], Any]] = [
    (
        "invite",
        lambda c: c.invitations.create("a@b.c", role="admin"),
        "POST",
        "/v1/invitations",
        {},
        {"invitee_email": "a@b.c", "role": "admin"},
    ),
    (
        "invite revoke",
        lambda c: c.invitations.revoke("i1"),
        "DELETE",
        "/v1/invitations/i1",
        {},
        None,
    ),
    (
        "claims",
        lambda c: c.profile.claims(),
        "GET",
        "/v1/me/profile",
        {},
        None,
    ),
    (
        "confirm",
        lambda c: c.profile.confirm("c1"),
        "PATCH",
        "/v1/me/profile/claims/c1",
        {},
        {"confirm": True},
    ),
    (
        "dismiss",
        lambda c: c.profile.dismiss("c1"),
        "PATCH",
        "/v1/me/profile/claims/c1",
        {},
        {"dismiss": True},
    ),
    (
        "forget",
        lambda c: c.profile.forget("c1"),
        "DELETE",
        "/v1/me/profile/claims/c1",
        {},
        None,
    ),
    (
        "create project profile",
        lambda c: c.profile.create_profile(project_id="p1", goals=["Map it"]),
        "POST",
        "/v1/knowledge/profiles",
        {},
        {"profile_type": "project", "project_id": "p1", "goals": ["Map it"]},
    ),
    (
        "create org profile",
        lambda c: c.profile.create_profile(name="Lab"),
        "POST",
        "/v1/knowledge/profiles",
        {},
        {"profile_type": "org", "name": "Lab"},
    ),
    ("workflow run", lambda c: c.workflows.get_run("r1"), "GET", "/v1/workflows/runs/r1", {}, None),
    # A project's sources (beakr sources).
    (
        "sources list",
        lambda c: c.project_sources.list("p1"),
        "GET",
        "/v1/projects/p1/sources",
        PAGE,
        None,
    ),
    (
        "sources attach",
        lambda c: c.project_sources.attach(
            "p1",
            provider="slack:nango",
            external_item_id="C1",
            external_item_type="channel",
            external_item_name="#assays",
            connector_id="c1",
            ingest_wiki=True,
            pull_mode="agent",
        ),
        "POST",
        "/v1/projects/p1/sources",
        {},
        {
            "provider": "slack:nango",
            "connector_id": "c1",
            "external_item_id": "C1",
            "external_item_type": "channel",
            "external_item_name": "#assays",
            "ingest_wiki": True,
            "pull_mode": "agent",
        },
    ),
    (
        "sources update",
        lambda c: c.project_sources.update(
            "p1", "s1", auto_sync_wiki=False, guidance="Only hand-offs", clear=["source_query"]
        ),
        "PATCH",
        "/v1/projects/p1/sources/s1",
        {},
        {"auto_sync_wiki": False, "guidance": "Only hand-offs", "source_query": None},
    ),
    (
        "sources remove",
        lambda c: c.project_sources.remove("p1", "s1"),
        "DELETE",
        "/v1/projects/p1/sources/s1",
        {},
        None,
    ),
    (
        "sources sync",
        lambda c: c.project_sources.sync("p1", "s1"),
        "POST",
        "/v1/projects/p1/sources/s1/sync",
        {},
        None,
    ),
    (
        "sources status",
        lambda c: c.project_sources.status("p1", "s1"),
        "GET",
        "/v1/projects/p1/sources/s1/sync-status",
        {},
        None,
    ),
    (
        "sources preview saved",
        lambda c: c.project_sources.preview("p1", "s1"),
        "POST",
        "/v1/projects/p1/sources/s1/rules/preview",
        {},
        {},
    ),
    (
        "sources preview no query",
        lambda c: c.project_sources.preview("p1", "s1", source_query=None, pull_mode="all"),
        "POST",
        "/v1/projects/p1/sources/s1/rules/preview",
        {},
        {"source_query": None, "pull_mode": "all"},
    ),
    (
        "sources preview item",
        lambda c: c.project_sources.preview_item(
            "p1",
            provider="gmail:nango",
            external_item_id="__all__",
            external_item_type="all",
            scope_mode="all",
            source_query={"gmail_q": "from:lab"},
        ),
        "POST",
        "/v1/projects/p1/sources/rules/preview",
        {},
        {
            "provider": "gmail:nango",
            "external_item_id": "__all__",
            "external_item_type": "all",
            "scope_mode": "all",
            "source_query": {"gmail_q": "from:lab"},
        },
    ),
    (
        "sources query keys",
        lambda c: c.project_sources.query_keys(),
        "GET",
        "/v1/integrations/source-query-keys",
        {},
        None,
    ),
]


@pytest.mark.parametrize(
    ("label", "invoke", "method", "path", "query", "body"), CASES, ids=[c[0] for c in CASES]
)
@respx.mock
def test_resource_request_shape(client, label, invoke, method, path, query, body) -> None:
    route = respx.route(method=method, url=f"{BASE}{path}").respond(
        200, json={"content": [], "outcome": {"status": "success"}, "id": "x"}
    )
    invoke(client)
    request = route.calls.last.request
    assert dict(request.url.params) == query
    if body is None:
        assert not request.content
    else:
        assert json.loads(request.content) == body
