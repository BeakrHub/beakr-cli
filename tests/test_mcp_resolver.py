"""Tests for the MCP server's _resolve_page reference resolver.

The resolver tries (in order): UUID -> /by-title -> /wiki/search fuzzy.
Verifies each branch and that the /by-slug path is no longer in use.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from beakr_cli.mcp_server import _resolve_page


@pytest.fixture(autouse=True)
def base_url(monkeypatch):
    """Pin a base URL so respx matches deterministically."""
    monkeypatch.setenv("BEAKR_API_URL", "https://api.example.com")
    monkeypatch.setenv("BEAKR_API_TOKEN", "test-token")


@pytest.mark.asyncio
@respx.mock
async def test_resolve_by_uuid() -> None:
    page_id = "00000000-0000-0000-0000-000000000001"
    respx.get(f"https://api.example.com/v1/knowledge/wiki/pages/{page_id}").mock(
        return_value=Response(200, json={"id": page_id, "title": "X", "project_id": None})
    )

    result = await _resolve_page(page_id)
    assert result is not None
    assert result["id"] == page_id


@pytest.mark.asyncio
@respx.mock
async def test_resolve_by_title_exact() -> None:
    """Title resolution hits /by-title -- not the legacy /by-slug."""
    respx.get("https://api.example.com/v1/knowledge/wiki/pages/by-title").mock(
        return_value=Response(
            200,
            json={"id": "11111111-1111-1111-1111-111111111111", "title": "Marcos Ortiz"},
        )
    )

    result = await _resolve_page("Marcos Ortiz")
    assert result is not None
    assert result["title"] == "Marcos Ortiz"

    # Sanity: the resolver did NOT call /by-slug
    by_slug_calls = [
        r for r in respx.calls if "/by-slug/" in str(r.request.url)
    ]
    assert by_slug_calls == []


@pytest.mark.asyncio
@respx.mock
async def test_resolve_falls_back_to_search() -> None:
    """If /by-title 404s, the resolver falls back to fuzzy /wiki/search."""
    page_id = "22222222-2222-2222-2222-222222222222"
    respx.get("https://api.example.com/v1/knowledge/wiki/pages/by-title").mock(
        return_value=Response(404, json={"detail": "not found"})
    )
    respx.get("https://api.example.com/v1/knowledge/wiki/search").mock(
        return_value=Response(200, json={"results": [{"id": page_id, "title": "Match"}]})
    )
    respx.get(f"https://api.example.com/v1/knowledge/wiki/pages/{page_id}").mock(
        return_value=Response(200, json={"id": page_id, "title": "Match"})
    )

    result = await _resolve_page("partial input")
    assert result is not None
    assert result["id"] == page_id


@pytest.mark.asyncio
@respx.mock
async def test_resolve_raises_on_total_miss() -> None:
    """All branches miss -> raises ValueError ("Page not found")."""
    respx.get("https://api.example.com/v1/knowledge/wiki/pages/by-title").mock(
        return_value=Response(404, json={"detail": "not found"})
    )
    respx.get("https://api.example.com/v1/knowledge/wiki/search").mock(
        return_value=Response(200, json={"results": []})
    )

    with pytest.raises(ValueError, match="Page not found"):
        await _resolve_page("nonexistent")


@pytest.mark.asyncio
@respx.mock
async def test_resolve_does_not_hit_by_slug() -> None:
    """Belt-and-suspenders: explicitly verify /by-slug is never called.

    After the by-slug route was removed in the engine, any call would
    404; the resolver must not depend on it.
    """
    respx.get("https://api.example.com/v1/knowledge/wiki/pages/by-title").mock(
        return_value=Response(404)
    )
    respx.get("https://api.example.com/v1/knowledge/wiki/search").mock(
        return_value=Response(200, json={"results": []})
    )

    try:
        await _resolve_page("anything")
    except ValueError:
        pass

    by_slug_calls = [r for r in respx.calls if "/by-slug" in str(r.request.url)]
    assert by_slug_calls == [], (
        f"Resolver should not hit /by-slug; observed {len(by_slug_calls)} calls."
    )
