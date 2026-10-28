"""A project id reaches the MCP tools for a credential restricted to projects.

Root cause guarded (sandbox e2e, 2026-10-27): the stdio server checked every
project it was given against the organization's listing (``GET /v1/projects``).
A credential restricted to specific projects is refused that listing ("not
addressed by a single project"), and one without ``projects:read`` is refused it
too, so ``documents`` and ``research`` failed with a 403 even when given the id
of the credential's own project. An id is now checked on its own route
(``GET /v1/projects/{id}``), and a 403 there passes it to the read, which
applies the credential's project limits itself.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli import mcp_tools_platform
from beakr_cli.mcp_server import mcp

API = "https://api.example.com"
PROJECT = "3e1dda3b-6b07-47fa-81b5-100e67e5b2e3"

pytestmark = [pytest.mark.asyncio, pytest.mark.real_project_refs]

_RESTRICTED = {
    "error": {
        "code": "insufficient_scope",
        "message": (
            "This credential is restricted to specific projects and cannot call an "
            "endpoint that is not addressed by a single project."
        ),
    }
}
_NO_PROJECTS_READ = {
    "error": {
        "code": "insufficient_scope",
        "message": "This credential lacks the 'projects:read' scope.",
    }
}


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")
    # Each test starts with no project resolved by an earlier one.
    monkeypatch.setattr(mcp_tools_platform, "_resolved_projects", {})


async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


def _documents() -> respx.Route:
    return respx.post(f"{API}/v1/knowledge/documents").mock(
        return_value=Response(200, json={"content": "1 match", "sources": {}, "is_error": False})
    )


@pytest.mark.parametrize("project_route", [(200, {"id": PROJECT}), (403, _RESTRICTED)])
@respx.mock
async def test_documents_reads_a_project_id_without_listing_the_organization(
    project_route: tuple[int, dict[str, Any]],
) -> None:
    listing = respx.get(f"{API}/v1/projects").mock(return_value=Response(403, json=_RESTRICTED))
    status, body = project_route
    respx.get(f"{API}/v1/projects/{PROJECT}").mock(return_value=Response(status, json=body))
    documents = _documents()

    result = await _call(
        "documents",
        {"command": "search", "arguments": {"query": "freezer"}, "project": PROJECT},
    )

    assert not result.isError, result.content
    assert json.loads(documents.calls.last.request.content)["project_id"] == PROJECT
    assert not listing.called


@respx.mock
async def test_research_reads_a_project_id_for_a_credential_without_projects_read() -> None:
    listing = respx.get(f"{API}/v1/projects").mock(
        return_value=Response(403, json=_NO_PROJECTS_READ)
    )
    respx.get(f"{API}/v1/projects/{PROJECT}").mock(
        return_value=Response(403, json=_NO_PROJECTS_READ)
    )
    research = respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=Response(200, json={"text": "Dana owns it.", "citations": [], "sources": {}})
    )

    result = await _call(
        "research", {"query": "Who owns ZETA-7?", "quick": True, "project": PROJECT}
    )

    assert not result.isError, result.content
    assert research.called
    assert PROJECT in research.calls.last.request.content.decode()
    assert not listing.called


@respx.mock
async def test_a_project_id_that_names_nothing_is_still_refused() -> None:
    respx.get(f"{API}/v1/projects/{PROJECT}").mock(
        return_value=Response(404, json={"error": {"code": "not_found", "message": "No."}})
    )
    documents = _documents()

    result = await _call("documents", {"command": "ls", "project": PROJECT})

    assert result.isError
    assert json.loads(result.content[0].text)["error_code"] == "scope_not_found"
    assert not documents.called
