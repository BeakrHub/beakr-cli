"""list_projects and get_profile answer on an installation without organization
administration (a box), without the organization line.

Root cause (Box 0.1.13 qualification, 17 Nov, amd64 and arm64): both tools open with
"Organization: ..." read from GET /v1/me/organization, which a box does not serve
(bare 404, so not_provided). The failure was not caught, so both tools answered
"This Beakr installation does not provide organization administration ... Do not retry
this tool here", although the project listing itself works there.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_cli import mcp_server

API = "https://api.test"
BARE = {"detail": "Not Found"}
PROJECT = {"id": "p1", "name": "Client check", "project_type": "standard"}


@pytest.fixture(autouse=True)
def box(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_box")


def _text(result) -> str:
    return "\n".join(part.text for part in result.content)


@pytest.mark.asyncio
@respx.mock
async def test_list_projects_answers_without_the_organization_line_on_a_box():
    respx.get(f"{API}/v1/me/organization").respond(404, json=BARE)
    respx.get(url__regex=rf"{API}/v1/projects(\?.*)?$").respond(
        json={"data": [PROJECT], "has_more": False, "next_cursor": None}
    )

    result = await mcp_server.mcp.call_tool("list_projects", {})

    text = _text(result)
    assert not result.is_error, text
    assert "Client check (standard, id: p1)" in text
    assert "not_provided" not in text and "organization administration" not in text
    assert not text.startswith("Organization:")


@pytest.mark.asyncio
@respx.mock
async def test_get_profile_answers_without_the_organization_line_on_a_box():
    respx.get(f"{API}/v1/me/organization").respond(404, json=BARE)
    respx.get(url__regex=rf"{API}/v1/.*profile.*").respond(
        json={"goals": ["Track assays"], "focus_areas": [], "profile_type": "org"}
    )

    result = await mcp_server.mcp.call_tool("get_profile", {"profile_type": "org"})

    text = _text(result)
    assert not result.is_error, text
    assert "organization administration" not in text


@pytest.mark.asyncio
@respx.mock
async def test_where_the_org_is_served_the_line_still_opens_list_projects():
    respx.get(f"{API}/v1/me/organization").respond(
        json={"id": "o1", "name": "Acme Labs", "slug": "acme"}
    )
    respx.get(url__regex=rf"{API}/v1/projects(\?.*)?$").respond(
        json={"data": [PROJECT], "has_more": False, "next_cursor": None}
    )

    result = await mcp_server.mcp.call_tool("list_projects", {})

    assert _text(result).startswith("Organization: Acme Labs")
    assert json.dumps("Client check")[1:-1] in _text(result)
