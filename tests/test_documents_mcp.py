import json

import pytest
import respx
from httpx import Response

from beakr_cli import mcp_server
from beakr_cli.cited_answer import research_lines


@pytest.mark.asyncio
async def test_real_stdio_tool_forwards_explicit_document_window(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "test-only")
    project = "11111111-1111-1111-1111-111111111111"
    with respx.mock:
        route = respx.post("https://api.test/v1/knowledge/documents").mock(
            return_value=Response(
                200, json={"content": "Lines 21-40", "sources": {}, "is_error": False}
            )
        )
        result = await mcp_server.mcp.call_tool(
            "documents",
            {
                "command": "read",
                "arguments": {"document": "memo", "start_line": 21, "end_line": 40},
                "project": project,
            },
        )
    assert not result.isError
    assert "Lines 21-40" in result.content[0].text
    assert json.loads(route.calls[0].request.content) == {
        "command": "read",
        "arguments": {"document": "memo", "start_line": 21, "end_line": 40},
        "project_id": project,
    }


@pytest.mark.parametrize("public", [True, False])
def test_terminal_research_does_not_hide_early_stops_or_cost(public):
    data = {
        "answer": "[verified: memo] finding",
        "sources": {},
        "checked": "one file",
        "not_checked": "other files",
        "stopped_early": True,
        "cost_usd": 0.15,
    }
    if public:
        data.update(text=data["answer"], citations=[])
    output = "\n".join(research_lines(data))
    assert "[cited: memo]" in output
    assert "Not checked: other files" in output
    assert "coverage is incomplete" in output
    assert '"cost_usd": 0.15' in output
