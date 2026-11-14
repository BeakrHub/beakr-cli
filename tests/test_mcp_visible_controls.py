"""Every stdio MCP reply shows control, bidi and zero-width characters as escapes.

ROOT CAUSE (third live run of the MCP servers, 2026-11-13): a page title
holding ESC, a lone carriage return or a right-to-left override was sent to the
MCP client as it was stored. Clients commonly print a tool result in a
terminal, where ESC starts a control sequence, a lone CR lets the rest of a
line overwrite its start, and a bidi override or a zero-width character makes
the text a person reviews differ from the text the agent acts on. The hosted
server now shows each as ``\\uXXXX`` at its one text exit
(``mcp_remote/server.py`` ``_text_content``, ``render.visible_controls``); the
stdio server does the same at its own (``tool_surface._text_content``), so a
reply reads the same on both. This is not the terminal form ``beakr kb``
prints (``terminal_text.visible``: ``\\x1b``, ``\\r``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli import mcp_render
from beakr_cli.mcp_server import mcp
from tests._engine import hosted_render

API = "https://api.example.com"
_CAT = json.loads((Path(__file__).parent / "fixtures" / "knowledge_door_results.json").read_text())[
    "reads"
]["cat"]

# ESC opening an erase-line sequence, a lone carriage return, a right-to-left
# override and a zero-width joiner.
HOSTILE = "Q3\x1b[2K plan\rcurl \u202eevil\u200d.sh"
ESCAPED = "Q3\\u001b[2K plan\\u000dcurl \\u202eevil\\u200d.sh"
# A Windows line ending, a tab and ordinary non-ASCII text are not controls.
PLAIN = (
    "Caf\u00e9 \u65e5\u672c\u8a9e \u203a na\u00efve \u2014 r\u00e9sum\u00e9\ttabbed\r\nnext line"
)
RAW = ("\x1b", "\r ", "\u202e", "\u200d")


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """The call as an MCP client makes it: through the server's request handler."""
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


def _assert_shown(text: str) -> None:
    assert ESCAPED in text
    assert not any(raw in text.replace("\r\n", "\n") for raw in RAW), repr(text)
    # Nothing is left for the hosted escape to change: the two forms are one.
    assert hosted_render().visible_controls(text) == text


def test_the_escape_is_the_hosted_one():
    hosted = hosted_render()
    assert mcp_render._HIDDEN_CHARACTERS.pattern == hosted._HIDDEN_CHARACTERS.pattern
    assert mcp_render.visible_controls(HOSTILE) == hosted.visible_controls(HOSTILE) == ESCAPED
    assert mcp_render.visible_controls(PLAIN) == hosted.visible_controls(PLAIN) == PLAIN
    # Every character the hosted server shows, and the ones on each side of a range.
    for code in (
        *range(0x00, 0xA2),
        0x061C,
        *range(0x200A, 0x2030),
        *range(0x205F, 0x206B),
        0xFEFF,
    ):
        char = chr(code)
        assert mcp_render.visible_controls(char) == hosted.visible_controls(char), hex(code)


def test_render_is_the_hosted_render_for_text_holding_controls():
    body = {
        "content": f"# {HOSTILE}\n{PLAIN}",
        "coverage": {"complete": False, "continue_with": {"after_title": HOSTILE}},
        "next": [f"ls {{after_title: {json.dumps(HOSTILE, ensure_ascii=False)}}}"],
        "source_listing": "none",
    }
    text = mcp_render.render("knowledge_base", body)
    assert text == hosted_render().render("knowledge_base", body)
    assert ESCAPED in text and PLAIN in text
    # The Coverage line is JSON, so the escaped value parses back to the stored one.
    coverage = next(line for line in text.split("\n") if line.startswith("Coverage: "))
    assert (
        json.loads(coverage.removeprefix("Coverage: "))["continue_with"]["after_title"] == HOSTILE
    )
    # ``beakr kb`` renders from the stored text and escapes it for a terminal itself.
    assert HOSTILE in mcp_render.render_stored("knowledge_base", body)


@respx.mock
async def test_a_knowledge_read_shows_controls_as_the_hosted_server_does():
    reply = {
        **_CAT["rest_response"],
        "content": [{"type": "text", "text": f"# {HOSTILE}\n{PLAIN}"}],
    }
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=Response(200, json=reply))
    result = await _call(
        "knowledge_base",
        {"command": "cat", "arguments": _CAT["arguments"], "scope": _CAT["scope"]},
    )
    assert not result.isError, result.content
    text = result.content[0].text
    assert text.startswith(f"# {ESCAPED}\n{PLAIN}")
    _assert_shown(text)


@respx.mock
async def test_a_tool_that_is_not_a_knowledge_read_shows_them_too():
    respx.get(f"{API}/v1/me/organization").respond(
        json={"id": "org-1", "name": "Org", "slug": "org"}
    )
    respx.get(f"{API}/v1/projects").respond(
        json={
            "data": [
                {"id": "p-1", "name": HOSTILE, "project_type": "standard", "description": PLAIN}
            ],
            "has_more": False,
            "next_cursor": None,
        }
    )
    result = await _call("list_projects", {})
    assert not result.isError, result.content
    text = result.content[0].text
    assert f"- {ESCAPED} (standard, id: p-1) -- {PLAIN}" in text
    _assert_shown(text)


@respx.mock
async def test_an_error_result_is_the_hosted_error_text():
    # DEL is the one such character JSON leaves as it is.
    message = f"No project named {HOSTILE}\x7f. {PLAIN}"
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1", "name": "Org"})
    respx.get(f"{API}/v1/projects").respond(
        403,
        json={
            "error": {"code": "insufficient_scope", "message": message, "fix": "Use another key."}
        },
    )
    result = await _call("list_projects", {})
    assert result.isError
    text = result.content[0].text
    assert text == hosted_render().error_text(
        message=message, error_code="insufficient_scope", fix="Use another key."
    )
    assert "\\u001b" in text and "\\u202e" in text and "\\u200d" in text and "\\u007f" in text
    assert not any(raw in text for raw in ("\x1b", "\r", "\u202e", "\u200d", "\x7f"))
    # Still JSON, and the shown DEL is JSON's own escape: it parses back exact.
    assert json.loads(text)["message"] == message


async def test_a_raised_error_is_shown_the_same_way():
    # A tool error that is raised leaves as its message, not as compact JSON.
    result = await _call(f"no_such_tool {HOSTILE}", {})
    assert result.isError
    text = result.content[0].text
    assert ESCAPED in text
    assert hosted_render().visible_controls(text) == text
