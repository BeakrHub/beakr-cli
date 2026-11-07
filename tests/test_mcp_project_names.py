"""Every stdio MCP tool that takes a project reads it from ``scope``, ``project`` or ``project_id``.

ROOT CAUSE (agent tool review, 2026-11-06): the project parameter had a
different name per tool -- ``scope`` on the Knowledge tools, ``project`` on
documents, read_file, context, conversations_search, import_conversation and
workboard, ``project`` and ``project_id`` on research and the graph tools -- and
only some tools read ``scope`` as an alias, so an agent carrying ``scope`` from
a Knowledge read was refused or, before the unknown-argument check, read every
project. Two names given with different values were also handled unevenly:
research given ``project`` and ``project_id`` read ``project_id`` and dropped
``project`` silently. Now every tool reads any of the three names, and two that
disagree are refused with ``invalid_arguments`` before anything is sent.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli import mcp_call_shaping
from beakr_cli.mcp_server import mcp

API = "https://api.example.com"
PROJECT_ID = "6f1f3a52-3c1e-4f43-8a51-0a4b7c9d2e10"
OTHER_ID = "4dd25154-0d80-44ab-b4c2-167319bfacb4"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


#: Each tool that takes a project, with the rest of a call that reaches its route.
_CALLS: dict[str, dict[str, Any]] = {
    "knowledge_base": {"command": "ls"},
    "knowledge_base_write": {"action": "archive", "arguments": {"page": "Old"}},
    "research": {"query": "Who owns billing?"},
    "wiki_stats": {},
    "wiki_graph": {},
    "documents": {"command": "ls"},
    "read_file": {"path": "memo.pdf"},
    "context": {},
    "conversations_search": {"query": "billing"},
    "import_conversation": {"client": "generic", "session_id": "s-1", "summary": "Decided X."},
    "workboard": {},
}


def test_every_tool_that_takes_a_project_is_named() -> None:
    # A tool added with a project parameter and no entry here would take only its own name.
    for tool in mcp._tool_manager.list_tools():
        names = set(tool.parameters.get("properties") or {}) & set(mcp_call_shaping.PROJECT_NAMES)
        if names:
            assert tool.name in mcp_call_shaping.PROJECT_PARAMETER, tool.name
            assert mcp_call_shaping.PROJECT_PARAMETER[tool.name] in names, tool.name
    assert set(_CALLS) | {"get_profile"} == set(mcp_call_shaping.PROJECT_PARAMETER)


def _sent_project(route: respx.Route) -> bool:
    """Whether any request after the project lookup named the project."""
    for call in route.calls:
        request = call.request
        if request.method == "GET" and request.url.path.endswith(f"/v1/projects/{PROJECT_ID}"):
            continue
        if PROJECT_ID in str(request.url) or PROJECT_ID.encode() in (request.content or b""):
            return True
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(_CALLS))
@pytest.mark.parametrize("name", ["scope", "project", "project_id"])
@respx.mock
async def test_any_project_name_reaches_the_route(tool: str, name: str) -> None:
    respx.get(f"{API}/v1/projects/{PROJECT_ID}").mock(
        return_value=Response(200, json={"id": PROJECT_ID, "name": "Research"})
    )
    route = respx.route(url__startswith=API).mock(return_value=Response(200, json={}))
    result = await _call(tool, {**_CALLS[tool], name: PROJECT_ID})
    text = result.content[0].text if result.content else ""
    assert "does not take" not in text, text
    assert _sent_project(route), (tool, name, text)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(_CALLS))
@respx.mock
async def test_two_names_that_disagree_are_refused_before_anything_is_sent(tool: str) -> None:
    route = respx.route(url__startswith=API).mock(return_value=Response(200, json={}))
    result = await _call(tool, {**_CALLS[tool], "scope": PROJECT_ID, "project_id": OTHER_ID})
    assert result.isError
    error = json.loads(result.content[0].text)
    assert error["error_code"] == "invalid_arguments"
    assert "name different projects" in error["message"]
    assert PROJECT_ID in error["message"] and OTHER_ID in error["message"]
    assert "scope" in error["fix"]
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_research_given_project_and_project_id_that_disagree_is_refused() -> None:
    # Both are declared on research: project_id used to win silently.
    route = respx.route(url__startswith=API).mock(return_value=Response(200, json={}))
    result = await _call(
        "research", {"query": "Who owns billing?", "project": PROJECT_ID, "project_id": OTHER_ID}
    )
    assert result.isError
    assert "`project`" in json.loads(result.content[0].text)["message"]
    assert not route.called


def test_names_that_agree_or_are_empty_are_one_project() -> None:
    declared = {"query", "project", "project_id", "quick", "sources"}
    assert mcp_call_shaping.normalize_arguments(
        "research", {"query": "q", "scope": PROJECT_ID, "project": PROJECT_ID}, declared
    ) == {"query": "q", "project": PROJECT_ID}
    # An empty declared value is no project: the alias fills it.
    assert mcp_call_shaping.normalize_arguments(
        "research", {"query": "q", "scope": PROJECT_ID, "project": ""}, declared
    ) == {"query": "q", "project": PROJECT_ID}
    assert mcp_call_shaping.normalize_arguments(
        "knowledge_base", {"command": "ls", "project_id": PROJECT_ID}, {"command", "scope"}
    ) == {"command": "ls", "scope": PROJECT_ID}
