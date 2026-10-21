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
