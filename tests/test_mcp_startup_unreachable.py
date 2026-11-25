"""``beakr mcp`` answers at once when its Beakr installation cannot be reached (B10).

Root cause guarded: before answering ``initialize`` the server read the
installation's OpenAPI document (which tools it serves), with a 10-second
timeout and the SDK's two retries. When connections to the installation hang
(a VPN that is down, a host that drops packets) that took about 31 seconds, and
on the self-hosted line a session start added up to 30 more: 34 to 63 seconds,
past the 30 seconds Claude Code waits for a server to start. Tool calls then
hung for minutes, because a connect waited out the 170-second research timeout
three times.

Now ``initialize`` is answered at once and the document is read in the
background; the listing follows what the last server read for that
installation (else the whole surface) and ``tools/list_changed`` corrects it
once the document arrives. A tool call gives up connecting after a few seconds
and says it cannot reach the installation.

A hanging connect is simulated in-process at the network backend: the connect
waits out whatever timeout httpx gives it and then fails, as a dropped SYN
does; with no timeout it never returns.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpcore
import httpx
import pytest
import respx
from mcp import ClientSession, types
from mcp.shared.memory import create_client_server_memory_streams

from beakr_cli import mcp_server, tool_surface
from beakr_cli.mcp_session import SESSION_SCOPE_TOOL

API = "https://api.test"

#: A box's document: no skills, integrations or organization administration.
BOX_DOCUMENT = {
    "paths": {
        "/v1/projects": {"get": {}, "post": {}},
        "/v1/context": {"post": {}},
        "/v1/knowledge/research": {"post": {}},
        "/v1/knowledge/wiki/command": {"post": {}},
    }
}

pytestmark = pytest.mark.real_capabilities


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("BEAKR_NO_UPDATE_CHECK", "1")
    monkeypatch.delenv(tool_surface.TOOLS_ENV, raising=False)


@pytest.fixture(autouse=True)
def fresh_server(monkeypatch):
    """The server is one process-wide object: each test starts it from scratch."""
    server = mcp_server.mcp
    monkeypatch.setattr(server._lowlevel_server, "instructions", server.instructions)
    for name, value in (
        ("_revealed", set()),
        ("_hidden", set()),
        ("_learned", None),
        ("_remembered", None),
        ("_learning", None),
        ("_last_read_started", -math.inf),
        ("_options", None),
        ("_listing_context", None),
    ):
        monkeypatch.setattr(server, name, value, raising=False)


@pytest.fixture
def hanging_connect(monkeypatch) -> list[float | None]:
    """Every connect hangs until its timeout, then fails; the timeouts it was given."""
    given: list[float | None] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        given.append(timeout)
        await anyio.sleep(math.inf if timeout is None else timeout)
        raise httpcore.ConnectTimeout(f"connect to {host}:{port} timed out")

    monkeypatch.setattr("httpcore._backends.auto.AutoBackend.connect_tcp", connect_tcp)
    return given


@asynccontextmanager
async def _served(handler=None) -> AsyncIterator[ClientSession]:
    """A client of ``run_stdio_async``, with stdio replaced by in-memory streams."""
    async with create_client_server_memory_streams() as (client_streams, server_streams):

        @asynccontextmanager
        async def stdio_server():
            yield server_streams

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(tool_surface, "stdio_server", stdio_server)
            async with anyio.create_task_group() as group:
                group.start_soon(mcp_server.mcp.run_stdio_async)
                async with ClientSession(*client_streams, message_handler=handler) as session:
                    yield session
                group.cancel_scope.cancel()


def _names(listing: types.ListToolsResult) -> set[str]:
    return {tool.name for tool in listing.tools}


async def test_initialize_and_the_listing_answer_at_once_while_connects_hang(
    hanging_connect,
) -> None:
    async with _served() as session:
        started = time.monotonic()
        with anyio.fail_after(2):
            initialized = await session.initialize()
        with anyio.fail_after(2):
            listing = await session.list_tools()
        elapsed = time.monotonic() - started
    assert elapsed < 2
    # Nothing read yet, nothing remembered: the whole surface, and instructions for it.
    # Self-hosted line: and session_scope, listed while the server holds a session
    # (its start is still in flight here, off the initialize path).
    assert _names(listing) == set(tool_surface.stdio_tools()) | {SESSION_SCOPE_TOOL}
    assert "'skills' to read a SKILL.md" in (initialized.instructions or "")
    # The document was being read, with the connect capped.
    assert hanging_connect and all(t is not None and t <= 5 for t in hanging_connect)


async def test_a_tool_call_says_it_cannot_reach_the_installation(
    hanging_connect, monkeypatch
) -> None:
    monkeypatch.setenv("BEAKR_CONNECT_TIMEOUT", "0.2")
    async with _served() as session:
        await session.initialize()
        with anyio.fail_after(10):
            result = await session.call_tool("list_projects", {})
    assert result.is_error
    error = json.loads(result.content[0].text)
    assert error["error_code"] == "unreachable"
    assert error["message"] == (
        f"Can't reach the Beakr installation at {API} (no connection within 0.2 seconds)."
    )
    assert "VPN" in error["fix"] and "list_projects" in error["fix"]
    # Every connect gave up at the cap, not at the 170-second call timeout.
    assert hanging_connect and max(t for t in hanging_connect if t is not None) <= 0.2


async def test_the_document_read_in_the_background_corrects_the_listing(monkeypatch) -> None:
    monkeypatch.setattr(tool_surface, "LISTING_GRACE_SECONDS", 0.05)
    release = anyio.Event()
    changed = anyio.Event()

    async def slow_document(request):
        await release.wait()
        return httpx.Response(200, json=BOX_DOCUMENT)

    async def handler(message) -> None:
        if isinstance(message, types.ToolListChangedNotification):
            changed.set()

    with respx.mock:
        respx.get(f"{API}/v1/public/openapi.json").mock(side_effect=slow_document)
        async with _served(handler) as session:
            with anyio.fail_after(2):
                await session.initialize()
                first = await session.list_tools()
            assert "skills" in _names(first)
            release.set()
            with anyio.fail_after(5):
                await changed.wait()
            second = await session.list_tools()
    assert {"skills", "integrations", "org_audit_log"}.isdisjoint(_names(second))
    assert {"research", "knowledge_base", "list_projects"} <= _names(second)
    assert "'skills'" not in (mcp_server.mcp._lowlevel_server.instructions or "")
    # Remembered for the next server, per installation.
    saved_file = Path(os.environ["BEAKR_HOME"]) / tool_surface.INSTALLATIONS_FILE
    saved = json.loads(saved_file.read_text())
    assert "skills" in saved[API]["unserved"]


async def test_a_remembered_installation_lists_and_instructs_right_while_unreachable(
    hanging_connect,
) -> None:
    tool_surface.remember_unserved(API, {"skills", "integrations"})
    async with _served() as session:
        with anyio.fail_after(2):
            initialized = await session.initialize()
            listing = await session.list_tools()
    assert "skills" not in _names(listing) and "integrations" not in _names(listing)
    assert "'skills'" not in (initialized.instructions or "")
    assert "'integrations'" not in (initialized.instructions or "")


def _beakr() -> str:
    found = shutil.which("beakr", path=str(Path(sys.executable).parent))
    assert found, "the beakr console script is not installed in this environment"
    return found


def test_the_installed_server_answers_initialize_long_before_a_hanging_api(
    isolated_home: Path,
) -> None:
    """End to end: ``beakr mcp`` against an API that accepts connections and never answers.

    Before the fix this took about 31 seconds (three 10-second reads of the
    document). Python's own start-up is in the bound, so it is looser than the
    in-process one, and still far under the 30 seconds a client allows.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                held.append(listener.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(isolated_home),
        "BEAKR_HOME": str(isolated_home / ".beakr"),
        "BEAKR_NO_KEYRING": "1",
        "BEAKR_API_URL": f"http://127.0.0.1:{listener.getsockname()[1]}",
        "BEAKR_API_KEY": "bk_live_startup_test",
        "BEAKR_NO_UPDATE_CHECK": "1",
    }
    started = time.monotonic()
    process = subprocess.Popen(
        [_beakr(), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=isolated_home,
        env=env,
        text=True,
    )
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.append(process.stdout.readline()), daemon=True)
    try:
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "startup-test", "version": "1"},
            },
        }
        process.stdin.write(json.dumps(initialize) + "\n")
        process.stdin.flush()
        reader.start()
        reader.join(timeout=10)
        elapsed = time.monotonic() - started
        assert lines, f"no initialize answer within {elapsed:.1f} s"
        answer = json.loads(lines[0])
        assert answer["id"] == 1 and "result" in answer
        assert elapsed < 10
    finally:
        process.kill()
        process.wait()
        listener.close()
        for connection in held:
            connection.close()
