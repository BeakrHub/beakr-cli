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


@pytest.mark.asyncio
@respx.mock
async def test_mcp_error_keeps_request_id_for_failed_search_diagnosis(monkeypatch):
    # The API told agents to report request_id but both error adapters dropped it.
    import json

    monkeypatch.setenv("BEAKR_API_KEY", "bk_test_x")
    monkeypatch.setenv("BEAKR_API_URL", API)
    respx.get(f"{API}/v1/projects").mock(
        return_value=httpx.Response(
            500,
            json={
                "error": {
                    "code": "internal_error",
                    "message": "Search failed",
                    "fix": "Report the request_id.",
                    "request_id": "req-search-123",
                },
            },
        )
    )
    result = await mcp_server.mcp.call_tool("list_projects", {})
    assert result.isError
    body = json.loads(result.content[0].text)
    assert body["request_id"] == "req-search-123"
    assert body["fix"] == "Report the request_id."


@pytest.mark.parametrize("request_id", ["bad\nvalue", "x" * 129, 123, None])
def test_malformed_request_ids_are_not_rendered(request_id):
    from beakr_cli.mcp_call_shaping import compact_error
    from beakr_cli.mcp_errors import error_fields

    response = httpx.Response(
        500,
        json={"error": {"message": "Failed", "request_id": request_id}},
        request=httpx.Request("GET", f"{API}/v1/x"),
    )
    assert "request_id" not in error_fields(response)
    assert "request_id" not in compact_error(
        "documents", {"message": "Failed", "request_id": request_id}
    )


def test_request_id_header_survives_non_json_gateway_error():
    from beakr_cli.mcp_errors import error_fields

    response = httpx.Response(
        502,
        text="bad gateway",
        headers={"X-Request-ID": "req-gateway-1"},
        request=httpx.Request("GET", f"{API}/v1/x"),
    )
    assert error_fields(response)["request_id"] == "req-gateway-1"
    assert "req-gateway-1" in describe_error(response)
