"""The stdio MCP server forgives the natural call and answers errors compactly.

Root causes (dogfood friction log rows 13, 14 and 17): ``knowledge_base`` with
``query`` beside ``command`` failed with "Usage: grep <query>" because options
were read only from ``arguments``; ``research`` with ``question`` failed with a
pydantic dump ending in an errors.pydantic.dev link; and every error result
carried ~120 tokens of null coverage fields. An agent paid a retry (or several)
to learn the calling convention on each.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli.mcp_server import mcp

API = "https://api.example.com"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


def _door_reply(text: str, *, status: str = "success", code: str | None = None) -> dict:
    outcome: dict[str, Any] = {"status": status}
    if code:
        outcome["error_code"] = code
    return {
        "content": [{"type": "text", "text": text}],
        "outcome": outcome,
        "sources": [],
        "coverage": {"command": "grep", "status": status, "returned": None, "total": None},
        "next": ["Correct the call as the message says and retry; nothing was read."],
    }


@pytest.mark.asyncio
@respx.mock
async def test_knowledge_budget_hint_survives_without_coverage_sidecar() -> None:
    # Live ls returned 137 of 271 pages. The API's only truncation warning
    # lived in next, which _wiki_result discarded unless coverage also existed.
    hint = "Output cut to 23241 of 49654 characters; page with limit and after_title."
    reply = _door_reply("271 page(s):\n  - [matter] First matter")
    reply.pop("coverage")
    reply["next"] = [hint]
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=Response(200, json=reply))
    result = await _call("knowledge_base", {"command": "ls", "arguments": {"limit": 500}})
    assert not result.isError
    assert hint in "\n".join(block.text for block in result.content if hasattr(block, "text"))


@pytest.mark.asyncio
@respx.mock
async def test_top_level_knowledge_options_are_folded_into_arguments() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=Response(200, json=_door_reply("1 page matched"))
    )
    result = await _call(
        "knowledge_base",
        {"command": "grep", "query": "lease", "limit": 5, "arguments": {"limit": 3}, "scope": "P"},
    )
    assert not result.isError, result.content
    sent = json.loads(route.calls.last.request.content)
    # Top-level options reach the door; an explicit ``arguments`` key wins.
    assert sent == {"command": "grep", "query": "lease", "limit": 3, "scope": "P"}


@pytest.mark.asyncio
@respx.mock
async def test_research_accepts_question_and_scope_as_aliases() -> None:
    route = respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=Response(200, json={"answer": "Five minutes.", "sources": {}})
    )
    project = "e56b7c9b-4616-4c89-94bc-6c1c49af35cc"
    result = await _call("research", {"question": "How long is a lease?", "scope": project})
    assert not result.isError, result.content
    sent = json.loads(route.calls.last.request.content)
    assert sent["query"] == "How long is a lease?"
    assert project in json.dumps(sent)


@pytest.mark.asyncio
async def test_a_validation_error_is_one_line_naming_the_fix() -> None:
    result = await _call("research", {"prompt": "How long is a lease?"})
    assert result.isError
    text = result.content[0].text
    assert "\n" not in text
    assert "pydantic" not in text
    assert text.startswith('research: missing `query` -- pass {"query": "..."}')
    assert "ignored unknown `prompt`" in text


@pytest.mark.asyncio
@respx.mock
async def test_an_error_outcome_is_compact() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=Response(
            200,
            json=_door_reply("Usage: grep <query>", status="error", code="invalid_arguments"),
        )
    )
    result = await _call("knowledge_base", {"command": "grep"})
    assert result.isError and result.structuredContent is None
    assert json.loads(result.content[0].text) == {
        "status": "error",
        "error_code": "invalid_arguments",
        "message": "Usage: grep <query>",
        "fix": 'pass {"command": "grep", "arguments": {"query": "..."}}',
    }


@pytest.mark.asyncio
@respx.mock
async def test_an_api_refusal_is_compact_with_the_envelopes_fix() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=Response(
            404,
            json={
                "detail": "Project not found",
                "error": {
                    "code": "not_found",
                    "message": "Project not found",
                    "fix": "Call list_projects for the names you can use.",
                },
            },
        )
    )
    result = await _call("knowledge_base", {"command": "ls", "scope": "Nope"})
    assert result.isError and result.structuredContent is None
    assert json.loads(result.content[0].text) == {
        "status": "error",
        "error_code": "not_found",
        "message": "Project not found",
        "fix": "Call list_projects for the names you can use.",
    }


@pytest.mark.asyncio
@respx.mock
async def test_research_resolves_a_project_name() -> None:
    # list_projects tells the agent to pass a project's name; the research
    # route takes an id and refused the name with "must be a valid UUID".
    project = "e56b7c9b-4616-4c89-94bc-6c1c49af35cc"
    respx.get(f"{API}/v1/projects").mock(
        return_value=Response(
            200,
            json={
                "data": [{"id": project, "name": "Platform Docs"}],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    route = respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=Response(200, json={"answer": "Five minutes.", "sources": {}})
    )
    result = await _call("research", {"query": "How long is a lease?", "scope": "platform docs"})
    assert not result.isError, result.content
    assert project in json.dumps(json.loads(route.calls.last.request.content))


@pytest.mark.asyncio
@respx.mock
async def test_research_returns_only_the_sources_its_answer_cites() -> None:
    # The researcher returns every source it read; the structured result used to
    # forward all of them (~16k tokens for a two-citation answer).
    read = {f"rag:{n}": {"key": f"rag:{n}", "type": "rag", "title": f"doc {n}"} for n in range(30)}
    respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=Response(
            200, json={"answer": "Five minutes.{{rag:7}} Renewed.{{rag:3}}", "sources": read}
        )
    )
    result = await _call("research", {"query": "How long is a lease?"})
    assert not result.isError, result.content
    text = result.content[0].text
    assert "doc 7" in text and "doc 3" in text
    assert "doc 12" not in text


@pytest.mark.asyncio
@respx.mock
async def test_research_numbers_each_document_once() -> None:
    # Proof run 2026-10-13: two passages of one file were listed as [1] and [2];
    # the hosted server's formatting.research numbers per document.
    one_file = {"type": "rag", "title": "runtime.md", "artifact_id": "f1"}
    respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=Response(
            200,
            json={
                "answer": "TTL 300s.{{rag:a}} Renewed by a heartbeat.{{rag:b}}",
                "sources": {
                    "rag:a": {**one_file, "key": "rag:a"},
                    "rag:b": {**one_file, "key": "rag:b"},
                },
            },
        )
    )
    result = await _call("research", {"query": "lease?"})
    text = result.content[0].text
    assert "TTL 300s.[1] Renewed by a heartbeat.[1]" in text
    assert "[1] {{rag:a}} {{rag:b}}  (rag) runtime.md" in text and "[2]" not in text


@pytest.mark.asyncio
@respx.mock
async def test_research_passes_quick_only_when_asked() -> None:
    route = respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=Response(200, json={"answer": "Five minutes.", "sources": {}})
    )
    await _call("research", {"query": "How long is a lease?", "quick": True})
    await _call("research", {"query": "How long is a lease?"})
    sent = [json.loads(call.request.content) for call in route.calls]
    assert sent == [
        {"query": "How long is a lease?", "quick": True},
        {"query": "How long is a lease?"},
    ]
