"""Headers the CLI and its MCP server send, now that both run on the SDK.

The CLI used to build its own httpx clients (``beakr_cli/client.py``); every
command and MCP tool now goes through ``beakr_sdk`` (developer platform W7,
SPEC-457). These tests carry over what that module guaranteed: the dev identity
(with its name) is forwarded, requests name the CLI's version, and writes carry
the run they belong to.
"""

from __future__ import annotations

import pytest
import respx

from beakr_cli import __version__
from beakr_cli.sdk import MCP_CLIENT_NAME, MCP_RUN_LABEL, get_async_sdk, get_sdk

API = "https://api.test"


@pytest.fixture(autouse=True)
def api_url(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)


def _headers_of_one_request() -> dict[str, str]:
    with respx.mock:
        route = respx.get(f"{API}/v1/me").respond(json={"user": {}})
        with get_sdk() as sdk:
            sdk.me.get()
        return dict(route.calls.last.request.headers)


def test_dev_identity_name_is_forwarded(monkeypatch) -> None:
    """Without X-Identity-Name the API keys the dev user as "seed" and silently
    provisions a new user who belongs to nothing, so every scoped read is 403 or
    empty. The eval harness sets BEAKR_DEV_IDENTITY_NAME expecting it to be sent.
    """
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "eval-user-id")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "eval@example.test")
    monkeypatch.setenv("BEAKR_DEV_IDENTITY_NAME", "eval-manifest")

    headers = _headers_of_one_request()

    assert headers["x-identity-id"] == "eval-user-id"
    assert headers["x-email"] == "eval@example.test"
    assert headers["x-identity-name"] == "eval-manifest"


def test_dev_identity_name_is_optional(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "seed-user-id")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "seed@example.test")

    assert "x-identity-name" not in _headers_of_one_request()


def test_requests_identify_the_cli_version(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", "key")

    headers = _headers_of_one_request()

    assert headers["user-agent"] == f"beakr-cli/{__version__}"
    assert headers["x-beakr-client"] == f"beakr-cli/{__version__}"


def test_a_declared_agent_and_run_are_forwarded(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", "key")
    monkeypatch.setenv("BEAKR_AGENT", "nightly-bot")
    monkeypatch.setenv("BEAKR_RUN", "run-7")

    headers = _headers_of_one_request()

    assert headers["x-beakr-agent"] == "nightly-bot"
    assert headers["x-beakr-run"] == "run-7"


@pytest.mark.asyncio
async def test_the_mcp_server_labels_its_writes_with_its_run(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", "key")
    with respx.mock:
        route = respx.get(f"{API}/v1/me").respond(json={"user": {}})
        async with get_async_sdk() as sdk:
            await sdk.me.get()
        headers = route.calls.last.request.headers
    assert headers["x-beakr-client"] == MCP_CLIENT_NAME
    assert headers["x-beakr-run"] == MCP_RUN_LABEL
    assert headers["user-agent"] == f"beakr-cli/{__version__}"


@pytest.mark.asyncio
async def test_research_outwaits_the_engines_route_timeout(monkeypatch) -> None:
    # Root cause: the stdio MCP server's client gave up after 90s and the
    # terminal's after 60s, but the engine gives research up to 150s, so slow
    # research answers came back as timeouts while the engine was still working.
    monkeypatch.setenv("BEAKR_API_KEY", "key")
    with respx.mock:
        route = respx.post(f"{API}/v1/knowledge/research").respond(json={"text": "ok"})
        async with get_async_sdk() as sdk:
            assert sdk.timeout == 170.0
            await sdk.knowledge.research("What changed?")
        assert route.calls.last.request.extensions["timeout"]["read"] == 170.0
        with get_sdk() as sdk:
            sdk.knowledge.research("What changed?")
        assert route.calls.last.request.extensions["timeout"]["read"] == 170.0


# X-Beakr-Session names the agent session behind a write, so its provenance can
# say "session <id>" beside the agent and run (engine: api_origin.session).
def test_a_declared_session_is_forwarded(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", "key")
    monkeypatch.setenv("BEAKR_SESSION", "nightly-7")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cc-session")

    assert _headers_of_one_request()["x-beakr-session"] == "nightly-7"


@pytest.mark.parametrize(
    "variable", ["CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CURSOR_CONVERSATION_ID"]
)
def test_the_host_agents_own_session_is_forwarded(monkeypatch, variable) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", "key")
    monkeypatch.setenv(variable, "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b")

    headers = _headers_of_one_request()

    assert headers["x-beakr-session"] == "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


def test_no_session_header_without_a_session(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", "key")

    assert "x-beakr-session" not in _headers_of_one_request()


@pytest.mark.asyncio
async def test_the_mcp_server_forwards_claude_codes_session(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", "key")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cc-session-1")
    with respx.mock:
        route = respx.get(f"{API}/v1/me").respond(json={"user": {}})
        async with get_async_sdk() as sdk:
            await sdk.me.get()
        headers = route.calls.last.request.headers
    assert headers["x-beakr-session"] == "cc-session-1"
