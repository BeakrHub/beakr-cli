from beakr_cli.mcp_server import _wiki_result, knowledge_base


def test_field_diagnostics_survive_cli_transport():
    diagnostic = {"reason": "unknown_field", "candidates": [
        {"field_id": "facts:example", "label": "Dispatch window"}
    ]}
    result = _wiki_result({"content": "No rows; discover fields.",
                           "outcome": {"status": "empty"},
                           "field_discovery": diagnostic})
    assert result["field_discovery"] == diagnostic
    assert result["status"] == "empty"


def test_tool_description_teaches_discovery_and_exact_selection():
    assert "view=fields" in knowledge_base.__doc__
    assert "field_id for exact rows" in knowledge_base.__doc__
    assert "Empty is not zero" in knowledge_base.__doc__
