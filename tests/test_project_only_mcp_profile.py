import pytest

from beakr_cli import mcp_server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments", [{"project_id": ""}, {"project_id": "p", "profile_type": "org"}]
)
async def test_org_profile_tool_calls_are_rejected_before_network(monkeypatch, arguments):
    async def unexpected_call(*args, **kwargs):
        pytest.fail("retired profile scope reached the API")

    monkeypatch.setattr(mcp_server, "_call_api", unexpected_call)
    assert "require a project_id" in await mcp_server.get_profile(**arguments)


@pytest.mark.asyncio
async def test_profile_tool_defaults_to_the_named_project(monkeypatch):
    from types import SimpleNamespace

    seen = []

    async def call(callback):
        return callback(
            SimpleNamespace(
                profile=SimpleNamespace(project=lambda project_id: seen.append(project_id))
            )
        )

    monkeypatch.setattr(mcp_server, "_call_api", call)
    assert await mcp_server.get_profile(project_id="p") == "No project profile found."
    assert seen == ["p"]
