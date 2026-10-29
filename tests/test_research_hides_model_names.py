"""Research output never names the model behind the answer; cost stays.

Root cause: the engine sent ``lead_model`` (an internal model name such as
"gpt-6-luna") in research responses, and the CLI printed it in the terminal
"Research usage" line, in ``--json`` and piped output, and in the MCP research
tool's ``research_usage``. Which model answered is internal (Max's call: drop
the model, keep the cost); ``cost_usd`` is a public field and is still shown.
An older engine may still send ``lead_model``, so the CLI drops it itself.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

from beakr_cli import mcp_render, mcp_result, mcp_server
from beakr_cli.main import app

API = "https://api.test"
_RESULT = {
    "text": "Lysis runs 5 minutes (sop.pdf › p. 2).",
    "answer": "Lysis runs 5 minutes {{rag:a-1}}.",
    "citations": [{"name": "sop.pdf", "filename": "sop.pdf", "location": {}}],
    "sources": {},
    "cost_usd": 0.042,
    "engine": "research_v2",
    "lead_model": "gpt-6-luna",
}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    with respx.mock:
        respx.post(f"{API}/v1/knowledge/research").mock(return_value=Response(200, json=_RESULT))
        yield


@pytest.mark.parametrize(
    "args",
    [
        ["research", "How long is lysis?"],
        ["research", "How long is lysis?", "--json"],
        ["kb", "research", "How long is lysis?"],
        ["kb", "research", "How long is lysis?", "--json"],
    ],
)
def test_research_output_has_the_cost_and_no_model_name(api, args, monkeypatch):
    monkeypatch.setenv("BEAKR_RESEARCH_USAGE_LOG", "")
    result = CliRunner().invoke(app, args, env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "gpt-6-luna" not in result.output and "lead_model" not in result.output
    if "--json" in args:
        assert json.loads(result.stdout)["cost_usd"] == 0.042


def test_the_terminal_rendering_keeps_the_cost(api, monkeypatch):
    from beakr_cli.cited_answer import print_research_result
    from beakr_cli.research_metadata import public_research

    lines = []
    monkeypatch.setattr(
        "beakr_cli.cited_answer.console.print", lambda text="", *a, **k: lines.append(str(text))
    )
    print_research_result(public_research(_RESULT))

    shown = "\n".join(lines)
    assert "gpt-6-luna" not in shown
    assert '"cost_usd": 0.042' in shown


@pytest.mark.asyncio
async def test_the_mcp_research_tool_names_no_model(api, tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("BEAKR_RESEARCH_USAGE_LOG", str(log))

    result = await mcp_server.research(query="How long is lysis?")

    text = mcp_render.render("research", mcp_result.finish(result, tool="research"))
    assert "gpt-6-luna" not in text and "lead_model" not in text
    assert '"cost_usd": 0.042' in text
    assert "gpt-6-luna" not in log.read_text()
