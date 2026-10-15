"""Pagination must describe the same visible rows on hosted and stdio MCP."""

from pathlib import Path

from tests._engine import engine_checkout


def test_document_listing_formatter_matches_engine():
    engine = engine_checkout("beakr/api/mcp_remote/document_listing.py")
    local = Path(__file__).resolve().parents[1] / "src/beakr_cli/document_listing.py"
    assert local.read_bytes() == engine.read_bytes()
