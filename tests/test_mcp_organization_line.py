"""The stdio server opens get_profile and list_projects with the hosted server's org line.

Root cause: a person in several orgs connected an agent and could not tell
which org its calls were acting in; no tool said. Both servers now lead those
two tools with ``Organization: <name> (<slug>) -- <what fixed it>.``, rendered
by ``organization_line``, which the stdio server carries a copy of
(beakr-engine ``beakr/api/mcp_remote/render.py``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from beakr_cli import mcp_render
from beakr_cli.mcp_server import _organization_pin
from tests._engine import hosted_render as load_hosted_render

ORG = {"id": "o1", "name": "Northwind Biosciences", "slug": "northwind-biosciences"}


@pytest.fixture(scope="module")
def hosted_render():
    return load_hosted_render()


@pytest.mark.parametrize(
    ("org", "pinned_by"),
    [
        (ORG, None),
        (ORG, "the API key"),
        (ORG, "this connection's URL (/mcp/o/northwind-biosciences)"),
        ({"name": "Acme\n\nLabs"}, None),
        ({}, "this CLI sign-in"),
    ],
)
def test_the_line_matches_the_hosted_server(hosted_render, org, pinned_by):
    assert mcp_render.organization_line(org, pinned_by=pinned_by) == (
        hosted_render.organization_line(org, pinned_by=pinned_by)
    )


def _sdk(*, kind="access_token", token=None, access_token=None, org_id=None):
    tokens = SimpleNamespace(access_token=access_token) if access_token else None
    return SimpleNamespace(
        credential=SimpleNamespace(kind=kind, token=token, tokens=tokens), org_id=org_id
    )


@pytest.mark.parametrize(
    ("sdk", "pinned_by"),
    [
        # An API key and a device token act in the org they were issued for,
        # whatever X-Org-Id says.
        (_sdk(kind="api_key", token="bk_live_x", org_id="other"), "the API key"),
        (_sdk(access_token="bk_at_device", org_id="other"), "this CLI sign-in"),
        (_sdk(token="bk_at_device"), "this CLI sign-in"),
        # A Clerk sign-in acts in the org the CLI names, else the active one.
        (
            _sdk(access_token="eyJclerk", org_id="acme"),
            "the CLI's org setting (beakr auth set-org or BEAKR_ORG_ID)",
        ),
        (_sdk(access_token="eyJclerk"), None),
        (_sdk(kind="dev_identity"), None),
    ],
)
def test_the_line_says_what_fixed_the_org(sdk, pinned_by):
    assert _organization_pin(sdk) == pinned_by
