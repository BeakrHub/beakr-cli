"""``beakr mcp`` over stdio answers every protocol version a client may speak.

Root cause guarded: the server was built on mcp 1.x, which knows protocol
versions only up to 2025-11-25. Clients on 2026-07-28 (recent Claude Code,
Codex and Cursor builds) open with ``server/discover``; mcp 1.x refused it, so
every connection paid a failed probe and a fallback to ``initialize``. On mcp
2.3 the server answers ``server/discover`` itself and still negotiates the
``initialize`` handshake at every earlier version.

Each test starts the installed ``beakr mcp`` as a client does, as a
subprocess speaking JSON-RPC on stdin and stdout, and checks what reaches
the client: the negotiated version, the instructions, the tool listing
(every tool's title and hints) and one harmless call. The handshake versions
are driven with raw JSON-RPC, byte for byte what an older client sends. The
2026-07-28 path goes through the mcp 2.3 client, the code a modern client runs.
The API URL points at a closed port, so no test reaches a network.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, types
from mcp.client.stdio import StdioServerParameters

from beakr_cli import __version__, mcp_server, tool_surface

#: Versions reached through ``initialize``, oldest first (mcp_types.version).
HANDSHAKE_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
MODERN_VERSION = "2026-07-28"
#: Seconds any one answer may take; the server starts in about two.
ANSWER_SECONDS = 60.0


def _beakr() -> str:
    """The ``beakr`` console script installed beside this interpreter."""
    found = shutil.which("beakr", path=str(Path(sys.executable).parent))
    assert found, "the beakr console script is not installed in this environment"
    return found


@pytest.fixture
def server_env(isolated_home: Path) -> dict[str, str]:
    """A signed-in environment whose API is a closed port and whose home is empty."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(isolated_home),
        "BEAKR_HOME": str(isolated_home / ".beakr"),
        "BEAKR_NO_KEYRING": "1",
        # Nothing listens on the discard port: capability and identity reads
        # fail at once, and the server lists every tool, as it does offline.
        "BEAKR_API_URL": "http://127.0.0.1:9",
        "BEAKR_API_KEY": "bk_live_stdio_protocol_test",
        "BEAKR_NO_UPDATE_CHECK": "1",
    }


class _RawClient:
    """A JSON-RPC peer on the server's stdio, with a timeout on every answer."""

    def __init__(self, env: dict[str, str], cwd: Path) -> None:
        self.process = subprocess.Popen(
            [_beakr(), "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            text=True,
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def request(self, request_id: int, method: str, params: dict | None = None) -> dict:
        self.send({"id": request_id, "method": method, **({"params": params} if params else {})})
        while True:
            line = self._lines.get(timeout=ANSWER_SECONDS)
            assert line is not None, f"the server exited: {self._stderr()}"
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    def _stderr(self) -> str:
        self.process.kill()
        assert self.process.stderr is not None
        return self.process.stderr.read()

    def close(self) -> int:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            return self.process.wait(timeout=ANSWER_SECONDS)
        finally:
            if self.process.poll() is None:
                self.process.kill()


def _expected_instructions() -> str:
    """The instructions with every tool listed, outside the eager mode, no update notice."""
    return mcp_server.instructions() + tool_surface.discovery_note()


def _assert_the_listing(tools: list[dict[str, Any]]) -> None:
    """Every stdio tool, in the surface's order, with its title and four hints.

    The self-hosted line's server then lists its own ``session_scope``, while it
    holds the session ``beakr mcp`` starts at startup (``mcp_session``).
    """
    from beakr_cli.mcp_session import SESSION_TOOLS

    assert [tool["name"] for tool in tools] == tool_surface.stdio_tools() + sorted(SESSION_TOOLS)
    for tool in tools:
        if tool["name"] in SESSION_TOOLS:
            continue
        name = tool["name"]
        expected = tool_surface.annotations(name).model_dump(
            by_alias=True, mode="json", exclude_none=True
        )
        assert tool["title"] == tool_surface.title(name), name
        assert tool["annotations"] == expected, name
        assert tool["description"] == tool_surface.description(name), name
        assert tool["inputSchema"]["type"] == "object", name
        assert "outputSchema" not in tool, name
    read_only = {tool["name"] for tool in tools if tool["annotations"]["readOnlyHint"]}
    assert read_only == {
        "knowledge_base",
        "list_projects",
        "read_file",
        "documents",
        "research",
        "integrations",
        "context",
        "skills",
        "workboard",
        "get_profile",
        "wiki_stats",
        "wiki_graph",
        "show_proposal",
        "review_options",
        "conversations_search",
        "org_audit_log",
        "upload_status",
        "knowledge_queue",
        "beakr_version",
        "discover_tools",
        # The self-hosted line's session tool.
        "session_status",
    }


def _assert_the_version_answer(text: str) -> None:
    assert text.startswith(f"Installed: beakr-cli {__version__} ("), text
    assert "Update checks are off" in text


@pytest.mark.parametrize("version", HANDSHAKE_VERSIONS)
def test_a_handshake_client_negotiates_its_own_version(server_env, tmp_path, version) -> None:
    client = _RawClient(server_env, tmp_path)
    try:
        initialized = client.request(
            1,
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "older-client", "version": "1.0"},
            },
        )["result"]
        client.send({"method": "notifications/initialized"})
        tools = client.request(2, "tools/list")["result"]["tools"]
        called = client.request(3, "tools/call", {"name": "beakr_version", "arguments": {}})
        pinged = client.request(4, "ping")
    finally:
        exit_code = client.close()

    assert initialized["protocolVersion"] == version
    assert initialized["serverInfo"] == {"name": "beakr", "version": __version__}
    assert initialized["capabilities"]["tools"] == {"listChanged": True}
    assert initialized["instructions"] == _expected_instructions()
    _assert_the_listing(tools)
    result = called["result"]
    assert result.get("isError") is False
    assert [block["type"] for block in result["content"]] == ["text"]
    assert "structuredContent" not in result
    _assert_the_version_answer(result["content"][0]["text"])
    # ping is a handshake-era request; the server still answers it there.
    assert pinged["result"] == {}
    assert exit_code == 0


def test_a_handshake_client_asking_for_an_unknown_version_gets_the_latest_handshake(
    server_env, tmp_path
) -> None:
    client = _RawClient(server_env, tmp_path)
    try:
        initialized = client.request(
            1,
            "initialize",
            {
                "protocolVersion": "2099-01-01",
                "capabilities": {},
                "clientInfo": {"name": "future-client", "version": "1.0"},
            },
        )["result"]
    finally:
        client.close()
    assert initialized["protocolVersion"] == HANDSHAKE_VERSIONS[-1]


def _parameters(env: dict[str, str], cwd: Path) -> StdioServerParameters:
    return StdioServerParameters(command=_beakr(), args=["mcp"], env=env, cwd=str(cwd))


async def _session_answers(client: Client) -> tuple[list[dict[str, Any]], types.CallToolResult]:
    listing = await client.list_tools()
    tools = [
        tool.model_dump(by_alias=True, mode="json", exclude_none=True) for tool in listing.tools
    ]
    return tools, await client.call_tool("beakr_version", {})


@pytest.mark.parametrize("mode", ["auto", MODERN_VERSION])
async def test_a_2026_07_28_client_connects_natively(server_env, tmp_path, mode) -> None:
    # "auto" is what current clients do: probe server/discover, and fall back to
    # initialize only when the server refuses it. mcp 1.x refused; this server
    # answers, so the probe is the whole handshake.
    info = types.Implementation(name="modern-client", version="1.0")
    async with Client(
        _parameters(server_env, tmp_path),
        mode=mode,
        client_info=info,
        cache=None,
        read_timeout_seconds=ANSWER_SECONDS,
    ) as client:
        assert client.protocol_version == MODERN_VERSION
        if mode == "auto":
            discovered = client.session.discover_result
            assert discovered is not None
            assert MODERN_VERSION in discovered.supported_versions
            assert discovered.instructions == _expected_instructions()
            assert discovered.capabilities.tools is not None
            assert discovered.capabilities.tools.list_changed is True
        tools, result = await _session_answers(client)
    _assert_the_listing(tools)
    assert result.is_error is False and result.structured_content is None
    assert [block.type for block in result.content] == ["text"]
    _assert_the_version_answer(result.content[0].text)


async def test_both_eras_list_the_same_tools(server_env, tmp_path) -> None:
    listings = []
    for mode in ("legacy", MODERN_VERSION):
        async with Client(
            _parameters(server_env, tmp_path),
            mode=mode,
            cache=None,
            read_timeout_seconds=ANSWER_SECONDS,
        ) as client:
            if mode == "legacy":
                assert client.protocol_version == HANDSHAKE_VERSIONS[-1]
            listings.append((await _session_answers(client))[0])
    legacy, modern = listings
    assert legacy == modern
