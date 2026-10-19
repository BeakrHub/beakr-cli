"""In a session pinned to one project, tools that read it by default do not offer `project`.

Root cause guarded: offered a `project` parameter in a session already pinned
to one project (BEAKR_PROJECT_ID or a directory binding), agents filled it in
anyway, mostly with names they made up, and the tool answered "No single
project named ..." instead of reading the pinned project.
"""

from __future__ import annotations

import asyncio

import pytest

from beakr_cli import mcp_server, project_binding, tool_surface


def _schemas() -> dict[str, dict]:
    return {tool.name: tool.inputSchema for tool in asyncio.run(mcp_server.mcp.list_tools())}


@pytest.fixture(autouse=True)
def _fresh_pin(monkeypatch):
    monkeypatch.setenv(tool_surface.TOOLS_ENV, "all")
    tool_surface._project_pinned.cache_clear()
    yield
    tool_surface._project_pinned.cache_clear()


def test_a_pinned_session_hides_project_from_the_tools_that_default_to_it(monkeypatch):
    monkeypatch.setattr(project_binding, "project_is_pinned", lambda: True)

    schemas = _schemas()

    for name in tool_surface.PINNABLE_TOOLS:
        properties = schemas[name].get("properties", {})
        assert not set(properties) & set(tool_surface.PROJECT_PARAMETERS), name
        assert not set(schemas[name].get("required", [])) & set(tool_surface.PROJECT_PARAMETERS)
    # The command itself is untouched.
    assert "command" in schemas["documents"]["properties"]


def test_an_unpinned_session_still_offers_project(monkeypatch):
    monkeypatch.setattr(project_binding, "project_is_pinned", lambda: False)

    schemas = _schemas()

    assert "project" in schemas["documents"]["properties"]
    assert "project" in schemas["research"]["properties"]


def test_tools_without_a_pinned_default_keep_project(monkeypatch):
    # These need a project named on every call; hiding it would leave them unusable.
    monkeypatch.setattr(project_binding, "project_is_pinned", lambda: True)

    schemas = _schemas()

    assert "project" in schemas["workboard"]["properties"]


def test_the_environment_pins_and_the_default_project_does_not(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BEAKR_PROJECT_ID", raising=False)
    assert project_binding.project_is_pinned() is False

    monkeypatch.setenv("BEAKR_PROJECT_ID", "00000000-0000-0000-0000-000000000001")
    assert project_binding.project_is_pinned() is True
