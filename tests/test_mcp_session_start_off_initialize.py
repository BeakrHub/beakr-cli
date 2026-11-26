"""``beakr mcp`` starts its session off the ``initialize`` path, and never runs a tool unscoped.

Self-hosted line only. B10 (Octant): the server took 34 to 63 seconds to answer
``initialize`` when its installation was unreachable, past the 30 seconds Claude
Code allows. Main's fix reads the installation's document in the background; on
this line the server also starts a session (``POST /v1/sessions``) before it
served anything (``scope_session.start_for_mcp``), which against an API that
accepts connections and never answers held ``initialize`` just as long.

Now the session is made and held before anything is answered, with no request
(``scope_session.prepare_for_mcp``), and started in the stdio loop's background
under the lock every tool call's ``McpSession.ensure_started`` takes. So
``initialize`` is answered at once, and a tool call that comes before the start
finishes waits for it and runs with the session's token, never the person's own
credential ("a transient error never hands the agent the person's whole reach").
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import anyio
import httpx
import pytest
import respx
from mcp import ClientSession, types
from mcp.shared.memory import create_client_server_memory_streams

from beakr_cli import mcp_server, scope_session, tool_surface
from beakr_cli.mcp_session import SESSION_SCOPE_TOOL

API = "https://api.test"
PERSON_KEY = "bk_live_person_key_for_tests"
SESSION_TOKEN = "bk_st_started_in_the_background"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.setenv("BEAKR_NO_UPDATE_CHECK", "1")
    # Sessions on, as on every self-hosted installation by default.
    monkeypatch.delenv(scope_session.MCP_SESSIONS_ENV, raising=False)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)
    monkeypatch.delenv(tool_surface.TOOLS_ENV, raising=False)
    scope_session.ACTIVE = None
    yield
    scope_session.ACTIVE = None


@pytest.fixture
def api() -> respx.MockRouter:
    """The mocked API; a route a test sets up need not be called."""
    with respx.mock(assert_all_called=False) as router:
        yield router


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


@asynccontextmanager
async def _served(handler=None) -> AsyncIterator[ClientSession]:
    """A client of ``run_stdio_async``, with stdio replaced by in-memory streams."""
    async with create_client_server_memory_streams() as (client_streams, server_streams):

        @asynccontextmanager
        async def stdio_server():
            yield server_streams

        # A server that cannot answer fails the test rather than hanging it.
        with pytest.MonkeyPatch.context() as patch, anyio.fail_after(20):
            patch.setattr(tool_surface, "stdio_server", stdio_server)
            async with anyio.create_task_group() as group:
                group.start_soon(mcp_server.mcp.run_stdio_async)
                async with ClientSession(*client_streams, message_handler=handler) as session:
                    yield session
                group.cancel_scope.cancel()


def _session() -> dict:
    """An automatic session as the API starts it, with its token."""
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return {
        "id": SID,
        "status": "active",
        "mode": "automatic",
        "agent_session": None,
        "client_name": "beakr mcp",
        "label": "beakr mcp",
        "folder": None,
        "remember_folder": False,
        "touched_projects": [],
        "read_projects": [],
        "write_projects": [],
        "picker_url": f"https://app.test/sessions/{SID}",
        "run_label": f"session:{SID}",
        "token": {"token": SESSION_TOKEN, "token_type": "bearer", "expires_at": later},
    }


def _names(listing: types.ListToolsResult) -> set[str]:
    return {tool.name for tool in listing.tools}


def _bearer(request: httpx.Request) -> str:
    return request.headers.get("authorization", "")


async def test_initialize_answers_at_once_while_the_session_start_never_answers(
    api: respx.MockRouter,
) -> None:
    """The API takes the connection and never answers ``POST /v1/sessions``."""
    asked = anyio.Event()
    requests: list[httpx.Request] = []

    async def never_answers(request):
        requests.append(request)
        asked.set()
        await anyio.sleep_forever()

    api.post(f"{API}/v1/sessions").mock(side_effect=never_answers)
    began = time.monotonic()
    with anyio.fail_after(5):
        async with _served() as session:
            initialized = await session.initialize()
            listing = await session.list_tools()
            elapsed = time.monotonic() - began
            await asked.wait()
            held = scope_session.ACTIVE
            # The session is held while its start is in flight, and has no token yet.
            assert held is not None and held.state is None
    # Far under the 30 seconds Claude Code waits for a server, with sessions on.
    assert elapsed < 2
    assert initialized.server_info.name
    # Its tools are listed: the server holds a session, as it did once started.
    assert SESSION_SCOPE_TOOL in _names(listing)
    # Started as the person (their credential starts it), never answered.
    assert [_bearer(request) for request in requests] == [f"Bearer {PERSON_KEY}"]


async def test_a_tool_call_before_the_start_finishes_waits_and_runs_scoped(
    api: respx.MockRouter,
) -> None:
    release = anyio.Event()
    asked = anyio.Event()

    async def slow_start(request):
        asked.set()
        await release.wait()
        return httpx.Response(201, json=_session())

    api.post(f"{API}/v1/sessions").mock(side_effect=slow_start)
    api.patch(f"{API}/v1/sessions/{SID}").respond(json=_session())
    api.get(url__regex=rf"{API}/v1/me.*").respond(json={"id": "org-1", "name": "Org"})
    projects = api.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": PROJECT, "name": "Pharma A"}], "has_more": False}
    )
    async with _served() as session:
        await session.initialize()
        with anyio.fail_after(2):
            await asked.wait()
        result: list[types.CallToolResult] = []

        async def call() -> None:
            result.append(await session.call_tool("list_projects", {}))

        async with anyio.create_task_group() as group:
            group.start_soon(call)
            # The call is in: it waits for the start, and has sent nothing yet.
            await anyio.sleep(0.3)
            assert not result
            assert not projects.called
            release.set()
            with anyio.fail_after(5):
                while not result:
                    await anyio.sleep(0.01)
    assert not result[0].is_error, result[0].content
    assert "Pharma A" in result[0].content[0].text
    assert projects.called
    # The tool's reads carried the session's token, never the person's key.
    for call_ in api.calls:
        request = call_.request
        if request.method == "POST" and request.url.path == "/v1/sessions":
            continue
        assert _bearer(request) == f"Bearer {SESSION_TOKEN}", (request.method, request.url)


async def test_a_tool_call_waiting_on_a_start_that_cannot_connect_says_so(
    api: respx.MockRouter,
) -> None:
    """The start fails to connect: the waiting call answers "can't reach", runs nothing."""
    release = anyio.Event()

    async def unreachable(request):
        await release.wait()
        raise httpx.ConnectError("connection refused", request=request)

    api.post(f"{API}/v1/sessions").mock(side_effect=unreachable)
    projects = api.get(f"{API}/v1/projects").respond(json={"data": [], "has_more": False})
    async with _served() as session:
        await session.initialize()
        result: list[types.CallToolResult] = []

        async def call() -> None:
            result.append(await session.call_tool("list_projects", {}))

        async with anyio.create_task_group() as group:
            group.start_soon(call)
            await anyio.sleep(0.2)
            release.set()
            with anyio.fail_after(10):
                while not result:
                    await anyio.sleep(0.01)
    assert result[0].is_error
    error = json.loads(result[0].content[0].text)
    assert error["error_code"] == "unreachable"
    assert "list_projects" in error["fix"]
    # Nothing ran with the person's credential in place of the session's.
    assert not projects.called


async def test_without_sessions_the_waiting_call_runs_as_before_and_the_listing_changes(
    api: respx.MockRouter,
) -> None:
    """An installation without sessions (404): the server runs as it always did there."""
    release = anyio.Event()
    changed = anyio.Event()

    async def no_sessions(request):
        await release.wait()
        return httpx.Response(404, json={"detail": "Not Found"})

    async def handler(message) -> None:
        if isinstance(message, types.ToolListChangedNotification):
            changed.set()

    api.post(f"{API}/v1/sessions").mock(side_effect=no_sessions)
    api.get(url__regex=rf"{API}/v1/me.*").respond(json={"id": "org-1", "name": "Org"})
    projects = api.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": PROJECT, "name": "Pharma A"}], "has_more": False}
    )
    async with _served(handler) as session:
        await session.initialize()
        first = await session.list_tools()
        assert SESSION_SCOPE_TOOL in _names(first)
        result: list[types.CallToolResult] = []

        async def call() -> None:
            result.append(await session.call_tool("list_projects", {}))

        async with anyio.create_task_group() as group:
            group.start_soon(call)
            await anyio.sleep(0.2)
            assert not projects.called
            release.set()
            with anyio.fail_after(5):
                while not result:
                    await anyio.sleep(0.01)
                await changed.wait()
        second = await session.list_tools()
        assert scope_session.ACTIVE is None
    assert not result[0].is_error, result[0].content
    assert _bearer(projects.calls.last.request) == f"Bearer {PERSON_KEY}"
    assert SESSION_SCOPE_TOOL not in _names(second)
