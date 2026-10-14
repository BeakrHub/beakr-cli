"""Research coverage and hidden model charges must survive both API shapes."""

import json

import pytest
import respx
from httpx import Response

from beakr_cli import mcp_render, mcp_result, mcp_server


@pytest.mark.asyncio
async def test_failed_research_records_unknown_cost_and_preserves_tool_error(monkeypatch, tmp_path):
    # A real research 504 lost its entire ledger row: accounting must distinguish
    # a failed attempted call from a successful zero-cost response.
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "test-only")
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("BEAKR_RESEARCH_USAGE_LOG", str(log))
    with respx.mock:
        respx.post("https://api.test/v1/knowledge/research").mock(
            return_value=Response(504, json={"detail": "Research agent timed out."})
        )
        result = await mcp_server.mcp.call_tool("research", {"query": "Private matter question"})
    assert result.isError
    assert "timed out" in result.content[0].text
    row = json.loads(log.read_text())
    assert row["cost_usd"] is None
    assert row["status"] == "error"
    assert row["error_type"] == "ApiCallError"
    assert row["client_duration_s"] >= 0
    assert "Private matter question" not in log.read_text()
    assert "test-only" not in log.read_text()


@pytest.mark.asyncio
async def test_cancelled_research_records_unknown_cost_without_swallowing_cancellation(
    monkeypatch, tmp_path
):
    import asyncio

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("BEAKR_RESEARCH_USAGE_LOG", str(log))

    async def cancelled(_request):
        raise asyncio.CancelledError()

    monkeypatch.setattr(mcp_server, "_call_api", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await mcp_server.research(query="Private matter question")
    row = json.loads(log.read_text())
    assert row["cost_usd"] is None
    assert row["status"] == "cancelled"
    assert row["error_type"] == "CancelledError"


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
async def test_research_preserves_coverage_and_backend_cost(public, monkeypatch, tmp_path):
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "test-only")
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("BEAKR_RESEARCH_USAGE_LOG", str(log))
    data = {
        "checked": "Two candidate records",
        "not_checked": "Remaining records",
        "cost_usd": 0.125,
        "stopped_early": True,
        "engine": "research_v2",
    }
    data.update(
        {"text": "One example [verified: report.docx].", "citations": []}
        if public
        else {"answer": "One example [verified: report.docx].", "sources": {}}
    )
    with respx.mock:
        respx.post("https://api.test/v1/knowledge/research").mock(
            return_value=Response(200, json=data)
        )
        result = await mcp_server.research(query="Count all records")
    text = mcp_render.render("research", mcp_result.finish(result, tool="research"))
    assert "Checked: Two candidate records" in text
    assert "Not checked: Remaining records" in text
    assert '"complete": false' in text
    assert '"cost_usd": 0.125' in text
    assert "[cited: report.docx]" in text
    assert "[verified:" not in text
    logged = json.loads(log.read_text())
    assert logged["cost_usd"] == 0.125
    assert "Count all records" not in log.read_text()


def test_unreported_cost_remains_unknown(monkeypatch, tmp_path):
    from beakr_cli.research_metadata import attach_research_metadata

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("BEAKR_RESEARCH_USAGE_LOG", str(log))
    attach_research_metadata({"content": "Answer"}, {})
    assert json.loads(log.read_text())["cost_usd"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
async def test_research_preserves_explicit_incomplete_coverage_without_early_stop(
    public, monkeypatch
):
    # Coverage can be incomplete without exhausting a time/step budget. Dropping
    # the API's coverage object lets finish() infer a misleading complete result.
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "test-only")
    monkeypatch.delenv("BEAKR_RESEARCH_USAGE_LOG", raising=False)
    data = {
        "coverage": {"complete": False, "scanned": 2, "has_more": True, "next_cursor": "page-2"},
        "stopped_early": False,
    }
    data.update(
        {"text": "Partial answer", "citations": []}
        if public
        else {"answer": "Partial answer", "sources": {}}
    )
    with respx.mock:
        respx.post("https://api.test/v1/knowledge/research").mock(
            return_value=Response(200, json=data)
        )
        result = await mcp_server.research(query="Find all records")
    body = mcp_result.finish(result, tool="research")
    assert body["coverage"]["complete"] is False
    text = mcp_render.render("research", body)
    assert '"scanned": 2' in text
    assert "page-2" in text


def test_cited_label_adapter_leaves_source_evidence_and_ordinary_prose_intact():
    from beakr_cli.research_metadata import with_cited_labels

    data = {
        "answer": "The auditor verified the amount. [verified: Audit] {{artifact:a}}",
        "sources": {"artifact:a": {"excerpt": "[verified: Audit]"}},
    }
    normalized = with_cited_labels(data)
    assert normalized["answer"] == (
        "The auditor verified the amount. [cited: Audit] {{artifact:a}}"
    )
    assert normalized["sources"] == data["sources"]
    assert "[verified: Audit]" in data["answer"]
