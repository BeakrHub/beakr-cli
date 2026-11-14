"""MCP writes name the agent that made them: the MCP client, unless BEAKR_AGENT says.

Root cause guarded: the stdio server sent ``X-Beakr-Agent`` only when
``BEAKR_AGENT`` was set, so every write from Claude Code or Codex was recorded
as ``beakr-mcp`` with no agent, and two agents sharing one key could not be
told apart. The server now sends the client's ``initialize`` clientInfo
(``claude-code/2.1.283``); ``BEAKR_AGENT`` still overrides it, and the plain
CLI at a terminal declares no agent (the person is the actor).
"""

from __future__ import annotations

import anyio
import pytest
import respx
from mcp import ClientSession, types
from mcp.shared.memory import create_client_server_memory_streams
from typer.testing import CliRunner

from beakr_cli import mcp_server
from beakr_cli.main import internal_app
from beakr_cli.sdk import client_agent

API = "https://api.test"
PROPOSAL = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


async def _dismiss_as(client_info: types.Implementation) -> respx.Route:
    """Dismiss a proposal through a real MCP session whose client declared ``client_info``."""
    route = respx.post(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/dismiss").respond(
        json={"proposal_id": PROPOSAL}
    )
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as group:
            lowlevel = mcp_server.mcp._mcp_server
            group.start_soon(
                lambda: lowlevel.run(*server_streams, mcp_server.mcp.initialization_options())
            )
            async with ClientSession(*client_streams, client_info=client_info) as session:
                await session.initialize()
                result = await session.call_tool("dismiss_proposal", {"proposal_id": PROPOSAL})
            group.cancel_scope.cancel()
    assert not result.isError, result
    return route


@respx.mock
async def test_a_write_names_the_mcp_client_as_its_agent() -> None:
    route = await _dismiss_as(types.Implementation(name="claude-code", version="2.1.283"))
    headers = route.calls.last.request.headers
    assert headers["X-Beakr-Agent"] == "claude-code/2.1.283"
    assert headers["X-Beakr-Client"].startswith("beakr-mcp/")


@respx.mock
async def test_beakr_agent_overrides_the_client(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_AGENT", "reviewer-2")
    route = await _dismiss_as(types.Implementation(name="claude-code", version="2.1.283"))
    assert route.calls.last.request.headers["X-Beakr-Agent"] == "reviewer-2"


@respx.mock
def test_the_cli_at_a_terminal_declares_no_agent() -> None:
    route = respx.get(f"{API}/v1/projects").respond(json={"data": [], "has_more": False})
    result = CliRunner().invoke(internal_app, ["projects", "ls", "--json"])
    assert result.exit_code == 0, result.output
    assert "X-Beakr-Agent" not in route.calls.last.request.headers


def test_the_client_label_is_header_safe() -> None:
    assert client_agent("Claude Code", "2.1") == "Claude Code/2.1"
    assert client_agent("codex\r\nX-Evil: 1", None) == "codex-X-Evil: 1"
    assert client_agent("", "1.0") is None
    assert len(client_agent("a" * 500, "1") or "") == 200


# The engine renders `<agent> for <person>, via <surface>` and names an agent by
# its slug (claude-code -> Claude Code, codex -> Codex). Codex's MCP client
# declares `codex-mcp-client`, which it printed as it is, and the CLI run by an
# agent declared nothing, so its writes read "beakr-cli 0.6.0 for ..." (sandbox
# run, 2026-11-14).


@respx.mock
async def test_codex_s_mcp_client_is_declared_as_codex() -> None:
    route = await _dismiss_as(types.Implementation(name="codex-mcp-client", version="0.58.0"))
    assert route.calls.last.request.headers["X-Beakr-Agent"] == "codex/0.58.0"


@respx.mock
async def test_another_mcp_client_is_declared_by_its_own_name() -> None:
    route = await _dismiss_as(types.Implementation(name="cursor-vscode", version="1.0"))
    assert route.calls.last.request.headers["X-Beakr-Agent"] == "cursor-vscode/1.0"


@respx.mock
@pytest.mark.parametrize(
    ("variable", "agent"),
    [
        ("CLAUDE_CODE_SESSION_ID", "claude-code"),
        ("CODEX_THREAD_ID", "codex"),
        ("CURSOR_CONVERSATION_ID", "cursor"),
    ],
)
def test_the_cli_run_by_an_agent_declares_that_agent(monkeypatch, variable, agent) -> None:
    monkeypatch.setenv(variable, "session-1")
    route = respx.get(f"{API}/v1/projects").respond(json={"data": [], "has_more": False})
    result = CliRunner().invoke(internal_app, ["projects", "ls", "--json"])
    assert result.exit_code == 0, result.output
    headers = route.calls.last.request.headers
    assert headers["X-Beakr-Agent"] == agent
    assert headers["X-Beakr-Session"] == "session-1"


@respx.mock
async def test_the_mcp_clients_name_wins_over_the_session_variable(monkeypatch) -> None:
    # Claude Code also sets its session variable for the MCP servers it starts.
    monkeypatch.setenv("CODEX_THREAD_ID", "t-1")
    route = await _dismiss_as(types.Implementation(name="claude-code", version="2.1.283"))
    assert route.calls.last.request.headers["X-Beakr-Agent"] == "claude-code/2.1.283"
