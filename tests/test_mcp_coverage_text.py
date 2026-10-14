from beakr_cli.mcp_render import render


def test_incomplete_scan_survives_text_only_transport_without_next_hint():
    # The server sends no structuredContent, so coverage cannot live only in JSON.
    text = render(
        "knowledge_base",
        {
            "content": "No matching rows.",
            "coverage": {"complete": False, "scanned": 5000},
            "next": [],
        },
    )
    assert '"complete": false' in text
    assert '"scanned": 5000' in text


def test_pagination_cursor_survives_text_only_transport():
    text = render(
        "documents",
        {"content": "Two files", "coverage": {"has_more": True, "next_cursor": "next-2"}},
    )
    assert "next-2" in text


def test_complete_results_do_not_gain_repetitive_metadata():
    assert (
        render("documents", {"content": "All files", "coverage": {"complete": True}}) == "All files"
    )
