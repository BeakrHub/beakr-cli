"""Project continuation survives the actual stdio MCP handler and generated SDK."""

import httpx
import respx

from tests import mcp_client


async def test_stdio_project_pages_expose_and_consume_the_rest_cursor(monkeypatch):
    api = "https://api.test"
    monkeypatch.setenv("BEAKR_API_URL", api)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_pagination_review")

    def page(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "1"
        second = request.url.params.get("cursor") == "second-page"
        return httpx.Response(
            200,
            json={
                "data": [{"id": "p2" if second else "p1", "name": "Second" if second else "First"}],
                "has_more": not second,
                "next_cursor": None if second else "second-page",
            },
        )

    with respx.mock as mock:
        route = mock.get(f"{api}/v1/projects").mock(side_effect=page)
        mock.get(f"{api}/v1/me/organization").mock(
            return_value=httpx.Response(200, json={"id": "o1", "name": "Northwind", "slug": "nw"})
        )
        result = await mcp_client.call_tool("list_projects", {"limit": 1})
        assert not result.is_error
        assert result.structured_content is None
        first = result.content[0].text
        # The continuation is in the one text block the client reads.
        assert "First" in first and 'cursor="second-page"' in first
        assert route.call_count == 1, "A bounded MCP page should not eagerly fetch every page"
        result = await mcp_client.call_tool(
            "list_projects", {"limit": 1, "cursor": "second-page"}
        )
        assert not result.is_error
        second = result.content[0].text
        assert "Second" in second and "cursor=" not in second
        assert route.call_count == 2
