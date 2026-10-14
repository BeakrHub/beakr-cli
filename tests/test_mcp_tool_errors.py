"""Stdio MCP tool errors say how to fix the call (developer platform W8.2.3).

Root cause: the stdio tools called ``response.raise_for_status()``, so a refused
call reached the agent as httpx's "Client error '404 Not Found' for url ...",
with nothing to act on. They now raise ``ApiCallError`` worded from the API's
error envelope (message, code and ``fix``), as the hosted server does.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from beakr_cli import mcp_server
from beakr_cli.mcp_errors import ApiCallError, describe_error

API = "https://api.thebeakr.com"


def _envelope(status: int, code: str, message: str, fix: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"detail": message, "error": {"code": code, "message": message, "fix": fix}},
        request=httpx.Request("GET", f"{API}/v1/x"),
    )


def test_the_error_text_is_the_message_and_the_fix():
    response = _envelope(404, "not_found", "Page not found", "List pages with ls first.")
    expected = "Page not found (not_found) To fix: List pages with ls first."
    assert describe_error(response) == expected


def test_a_response_without_an_envelope_still_says_what_failed():
    response = httpx.Response(502, text="bad gateway", request=httpx.Request("GET", f"{API}/v1/y"))
    assert describe_error(response) == "HTTP 502 from /v1/y"


@pytest.mark.asyncio
@respx.mock
async def test_a_refused_tool_call_raises_the_fix(monkeypatch):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_test_x")
    monkeypatch.setenv("BEAKR_API_URL", API)
    respx.get(f"{API}/v1/projects").mock(
        return_value=_envelope(
            403,
            "insufficient_scope",
            "Missing scope projects:read",
            "Use a key granted projects:read.",
        )
    )
    with pytest.raises(ApiCallError) as raised:
        await mcp_server.list_projects()
    assert "To fix: Use a key granted projects:read." in str(raised.value)
    # Still an HTTPStatusError, so callers that read the response keep working.
    assert isinstance(raised.value, httpx.HTTPStatusError)
    assert raised.value.response.status_code == 403
