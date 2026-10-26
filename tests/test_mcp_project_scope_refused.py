"""A project the MCP tools are given must name one the caller reads, or be refused.

Y12 (Max's box, 0.1.8, a fresh ``beakr mcp``): ``documents`` and ``read_file``
answered for every project the caller can see when the project they were given
named nothing: ``read_file`` had no project argument, so a ``scope`` was dropped,
and ``documents`` passed an unknown project id straight to the route, which
listed nothing ("0 in scope"). Now every tool that takes a project resolves it
here, an id included, and a miss is refused as ``scope_not_found`` with the
closest names, as ``beakr --project`` refuses one. Y13: get_profile's refusal
read "profile_type='project' requires profile_type='org' or project_id", a
project name came back as a UUID parser's dump, and its goals ran together.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli.mcp_server import mcp

API = "https://api.example.com"
SCIENCE = "9939752a-9d48-475c-b72e-1d3fc46c064a"
PROJECTS = [
    {"id": SCIENCE, "name": "Science Ingest 018", "slug": "science-ingest-018"},
    {"id": "cd15d5e9-45be-4b3e-ace6-a04e839731f9", "name": "Max interviews"},
]
UNKNOWN = "00000000-0000-0000-0000-000000000001"

pytestmark = [pytest.mark.asyncio, pytest.mark.real_project_refs]


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


def _projects() -> respx.Route:
    return respx.get(f"{API}/v1/projects").mock(
        return_value=Response(200, json={"data": PROJECTS, "has_more": False})
    )


async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("documents", {"command": "ls", "scope": "Science Ingst"}),
        ("documents", {"command": "ls", "project": UNKNOWN}),
        ("read_file", {"artifact_id": "Reagents.xlsx", "scope": "Science Ingst"}),
        ("read_file", {"artifact_id": "Reagents.xlsx", "project": UNKNOWN}),
        ("wiki_stats", {"project": "Science Ingst"}),
        ("research", {"query": "x", "project": UNKNOWN}),
        ("get_profile", {"project_id": "Science Ingst"}),
    ],
)
@respx.mock
async def test_a_project_that_names_nothing_is_refused_with_the_closest(
    tool: str, arguments: dict[str, Any]
) -> None:
    _projects()
    reads = respx.route(path__regex=r"^/v1/(knowledge|artifacts)/").mock(
        return_value=Response(200, json={})
    )

    result = await _call(tool, arguments)

    assert result.isError
    body = json.loads(result.content[0].text)
    assert body["error_code"] == "scope_not_found"
    assert body["message"].startswith("No project you can read matches")
    if "Ingst" in json.dumps(arguments):
        assert f"Science Ingest 018 (science-ingest-018, {SCIENCE})" in body["fix"]
    # Nothing was read for every project instead.
    assert not reads.called


@respx.mock
async def test_read_file_keeps_to_the_project_it_names() -> None:
    _projects()
    read = respx.post(f"{API}/v1/artifacts/read").mock(
        return_value=Response(
            200,
            json={
                "file": {
                    "id": SCIENCE,
                    "version_id": None,
                    "title": "Reagents.xlsx",
                    "display_path": "Reagents.xlsx",
                },
                "unit": None,
                "text": "x",
                "start": 0,
                "end": 1,
                "total_chars": 1,
                "has_more": False,
            },
        )
    )

    by_scope = await _call(
        "read_file", {"artifact_id": "Reagents.xlsx", "scope": "science ingest 018"}
    )
    assert not by_scope.isError, by_scope.content
    assert json.loads(read.calls.last.request.content)["project"] == SCIENCE

    unscoped = await _call("read_file", {"artifact_id": "Reagents.xlsx"})
    assert not unscoped.isError
    assert "project" not in json.loads(read.calls.last.request.content)


@respx.mock
async def test_get_profile_reads_a_project_by_name_with_each_goal_on_its_line() -> None:
    _projects()
    profile = respx.get(f"{API}/v1/knowledge/profiles/project/{SCIENCE}").mock(
        return_value=Response(
            200,
            json={
                "name": "Biotech lab data",
                "goals": [
                    "Track sequencing runs, samples, variants and assay results",
                    "Find which compounds and constructs were used where",
                ],
                "entity_types": ["sample", "compound"],
            },
        )
    )

    respx.get(f"{API}/v1/me/organization").mock(
        return_value=Response(200, json={"id": "o1", "name": "Northwind", "slug": "northwind"})
    )

    result = await _call("get_profile", {"project_id": "Science Ingest 018"})

    assert not result.isError, result.content
    assert profile.called
    text = result.content[0].text
    # The org it was read in leads (an API key acts in the org it was issued for).
    assert text.startswith(
        "Organization: Northwind (northwind) -- pinned by the API key.\n\n"
        "# Biotech lab data (project profile)"
    )
    assert "Goals:\n  - Track sequencing runs, samples, variants and assay results\n" in text
    assert "  - Find which compounds and constructs were used where" in text
    assert "Entity types:\n  - sample\n  - compound" in text


async def test_get_profile_without_a_project_says_what_to_pass() -> None:
    result = await _call("get_profile", {"profile_type": "project"})

    assert result.isError
    body = json.loads(result.content[0].text)
    assert body["error_code"] == "invalid_arguments"
    assert body["message"] == "A project profile needs the project: pass project_id."
    assert "requires profile_type='org'" not in result.content[0].text
