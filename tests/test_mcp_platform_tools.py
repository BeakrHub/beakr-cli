"""MCP platform tools: what they send, that they identify as MCP, and that output is bounded."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from beakr_cli import __version__
from beakr_cli.mcp_tools_platform import (
    MAX_LIST_ITEMS,
    MAX_TEXT_CHARS,
    context,
    conversations_search,
    import_conversation,
    skills,
)

API = "https://api.test"
PROJECT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
async def test_context_posts_and_identifies_as_mcp() -> None:
    route = respx.post(f"{API}/v1/context").mock(
        return_value=httpx.Response(200, json={"content": "# You\nPlatform lead"})
    )

    text = await context(project=PROJECT_ID, question="billing", budget_chars=999_999)

    assert "Platform lead" in text
    request = route.calls.last.request
    assert request.headers["X-Beakr-Client"] == f"beakr-mcp/{__version__}"
    assert json.loads(request.content) == {
        "format": "markdown",
        "budget_chars": 20_000,
        "project_id": PROJECT_ID,
        "question": "billing",
    }


@respx.mock
async def test_context_resolves_a_project_name() -> None:
    """context takes the project name list_projects prints, as every other tool does.

    Root cause guarded: the name went to /v1/context as ``project_id``, a UUID
    there, so a name was refused with a bare HTTP 422.
    """
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": PROJECT_ID, "name": "Platform"}], "has_more": False}
    )
    route = respx.post(f"{API}/v1/context").respond(json={"content": "ctx"})

    assert await context(project="platform") == "ctx"
    assert json.loads(route.calls.last.request.content)["project_id"] == PROJECT_ID

    with pytest.raises(ValueError, match="No project you can read matches 'Nope'"):
        await context(project="Nope")

    await context()
    assert "project_id" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_a_validation_refusal_keeps_the_field_and_its_text() -> None:
    """A 422 reads as the API's field errors, never "HTTP 422"."""
    from beakr_cli import mcp_server

    respx.post(f"{API}/v1/context").respond(
        422,
        json={
            "detail": [
                {
                    "loc": ["body", "budget_chars"],
                    "msg": "Input should be greater than or equal to 2000",
                    "type": "greater_than_equal",
                }
            ]
        },
    )
    result = await mcp_server.mcp.call_tool("context", {"budget_chars": 600})
    text = result.content[0].text
    assert result.isError
    assert "budget_chars: Input should be greater than or equal to 2000" in text
    assert "HTTP 422" not in text


@respx.mock
async def test_context_output_is_bounded() -> None:
    respx.post(f"{API}/v1/context").mock(
        return_value=httpx.Response(200, json={"content": "x" * (MAX_TEXT_CHARS + 500)})
    )
    # The bound is applied where every tool's answer is shaped (mcp_result.finish,
    # SPEC-488), and cutting the text marks the result incomplete with a hint.
    from beakr_cli import mcp_server

    result = await mcp_server.mcp._tool_manager.call_tool("context", {}, convert_result=False)
    assert len(result["content"]) < MAX_TEXT_CHARS + 200
    assert "truncated" in result["content"]
    assert result["coverage"]["complete"] is False
    assert result["next"] and "budget_chars" in result["next"][0]


async def test_context_rejects_unknown_format_without_a_request() -> None:
    assert "Invalid format" in await context(format="yaml")


@respx.mock
async def test_skills_lists_a_bounded_library() -> None:
    # One page at a time (SPEC-488): the page shows its rows and says how to go on.
    first = [{"id": f"s{i}", "slug": f"skill-{i}", "name": f"S{i}"} for i in range(MAX_LIST_ITEMS)]
    route = respx.get(f"{API}/v1/skills").mock(
        return_value=httpx.Response(
            200, json={"data": first, "has_more": True, "next_cursor": "page-2"}
        )
    )
    result = await skills()
    text = result["content"]
    assert "skill-0" in text and f"skill-{MAX_LIST_ITEMS - 1}" in text
    assert route.calls[0].request.url.params["limit"] == str(MAX_LIST_ITEMS)
    assert result["coverage"] == {
        "returned": MAX_LIST_ITEMS,
        "has_more": True,
        "next_cursor": "page-2",
        "complete": False,
    }
    assert 'cursor="page-2"' in text and 'cursor="page-2"' in result["next"][0]


@respx.mock
async def test_skills_reads_one_skill_md_by_slug() -> None:
    respx.get(f"{API}/v1/skills").mock(
        return_value=httpx.Response(200, json=[{"id": "s1", "slug": "deploy", "name": "Deploy"}])
    )
    bundle = respx.get(f"{API}/v1/skills/s1/bundle").mock(
        return_value=httpx.Response(
            200,
            json={"files": {"SKILL.md": "---\nname: deploy\n---\nSteps", "run.sh": "echo"}},
        )
    )
    text = (await skills("deploy"))["content"]
    assert bundle.called
    assert "Steps" in text
    assert "run.sh" in text


@respx.mock
async def test_skills_unknown_slug_says_how_to_list() -> None:
    respx.get(f"{API}/v1/skills").mock(return_value=httpx.Response(200, json=[]))
    assert "no argument" in (await skills("missing"))["content"]


@respx.mock
async def test_conversations_search_sends_filters_and_caps_limit() -> None:
    route = respx.get(f"{API}/v1/conversations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "cv1",
                        "title": "Importer",
                        "client": "codex",
                        "summary": "Fixed the importer",
                        "updated_at": "2026-10-01T00:00",
                    }
                ],
                "has_more": True,
                "next_cursor": "cv-page-2",
            },
        )
    )
    result = await conversations_search(
        query="importer", client="codex", project=PROJECT_ID, limit=999
    )
    text = result["content"]
    params = route.calls.last.request.url.params
    assert (params["q"], params["client"], params["project_id"]) == (
        "importer",
        "codex",
        PROJECT_ID,
    )
    assert params["limit"] == str(MAX_LIST_ITEMS)
    assert "Importer" in text and "cv1" in text
    # The next page is reachable, with the same filters (SPEC-488).
    assert result["coverage"]["next_cursor"] == "cv-page-2"
    assert 'cursor="cv-page-2" and the same filters' in text
    await conversations_search(
        query="importer", client="codex", project=PROJECT_ID, cursor="cv-page-2"
    )
    assert route.calls.last.request.url.params["cursor"] == "cv-page-2"


@respx.mock
async def test_conversations_search_with_a_query_uses_the_index() -> None:
    route = respx.get(f"{API}/v1/conversations/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "kind": "imported",
                        "id": "cv1",
                        "title": "Pump calibration",
                        "summary": "Chose the March curve",
                        "updated_at": "2026-10-01T00:00",
                        "findings": [{"page_title": "Pump calibration"}],
                        "outputs": [],
                    },
                    {
                        "kind": "ask_thread",
                        "id": "t1",
                        "title": "Pump curves",
                        "updated_at": "2026-09-30T00:00",
                        "findings": [],
                        "outputs": [{"title": "Curve memo"}],
                    },
                ]
            },
        )
    )
    text = (await conversations_search(query="which pump curve", project=PROJECT_ID, limit=99))[
        "content"
    ]
    params = route.calls.last.request.url.params
    assert (params["q"], params["match"], params["project_id"]) == (
        "which pump curve",
        "any",
        PROJECT_ID,
    )
    assert params["limit"] == "25"
    assert "imported session" in text and "Beakr chat" in text
    assert "findings: 1 (Pump calibration)" in text and "outputs: Curve memo" in text


@respx.mock
async def test_import_conversation_body() -> None:
    route = respx.post(f"{API}/v1/conversations/import").mock(
        return_value=httpx.Response(
            200, json={"conversation_id": "cv1", "created": True, "stored": "summary"}
        )
    )
    text = await import_conversation(
        client="claude-code",
        session_id="sess-1",
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        summary="Greeted",
        project=PROJECT_ID,
    )
    assert "Imported conversation cv1" in text
    assert json.loads(route.calls.last.request.content) == {
        "client": "claude-code",
        "session_id": "sess-1",
        # An agent-composed import has no host marker that a person typed it.
        "interactive": False,
        # No store_full_text: the person's per-client setting decides.
        "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        "summary": "Greeted",
        "project_id": PROJECT_ID,
    }


@pytest.mark.parametrize("query", ["", "which pump curve"])
@respx.mock
async def test_conversations_search_resolves_a_project_name(query: str) -> None:
    # Root cause: the name went to the routes as project_id, a UUID there, and
    # was refused "Input should be a valid UUID".
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": PROJECT_ID, "name": "Fix Verify C 017"}], "has_more": False}
    )
    route = respx.get(url__regex=rf"{API}/v1/conversations(/search)?\?").respond(
        json={"data": [], "has_more": False}
    )

    await conversations_search(query=query, project="Fix Verify C 017")

    assert route.calls.last.request.url.params["project_id"] == PROJECT_ID


@respx.mock
async def test_import_conversation_resolves_a_project_name() -> None:
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": PROJECT_ID, "name": "Fix Verify C 017"}], "has_more": False}
    )
    route = respx.post(f"{API}/v1/conversations/import").respond(
        json={"conversation_id": "cv1", "created": True, "stored": "summary"}
    )

    await import_conversation(
        client="claude-code", session_id="s", summary="Greeted", project="Fix Verify C 017"
    )

    assert json.loads(route.calls.last.request.content)["project_id"] == PROJECT_ID


@respx.mock
async def test_import_conversation_asks_for_its_own_summary_when_one_is_required() -> None:
    # Root cause: the route's fix names store_full_text, compound and hold_text,
    # which this tool does not take, so an agent following it could not comply.
    from beakr_cli.mcp_errors import ApiCallError

    fix = (
        "Send a `summary` with the messages, set `store_full_text: true` to keep the "
        "transcript, or set `compound: true` (or `hold_text: true` ...)."
    )
    message = "This import sends messages but no summary, so nothing from it would be kept."
    respx.post(f"{API}/v1/conversations/import").respond(
        422,
        json={
            "detail": {"code": "summary_required", "message": message, "fix": fix},
            "error": {"code": "summary_required", "message": message, "fix": fix},
        },
    )

    with pytest.raises(ApiCallError) as raised:
        await import_conversation(
            client="claude-code", session_id="s", messages=[{"role": "user", "content": "hi"}]
        )

    fields = raised.value.fields
    assert fields["error_code"] == "summary_required"
    assert "`summary`" in str(fields["fix"])
    for absent in ("store_full_text", "compound", "hold_text"):
        assert absent not in str(fields["fix"]) and absent not in str(raised.value)


async def test_import_conversation_validates_client() -> None:
    assert "Invalid client" in await import_conversation(client="vim", session_id="s", summary="x")


async def test_platform_tools_are_registered_on_the_server() -> None:
    from mcp.server.fastmcp import FastMCP

    from beakr_cli.mcp_server import mcp

    # Registered (callable) whether or not the default listing shows them.
    names = {tool.name for tool in await FastMCP.list_tools(mcp)}
    assert {
        "context",
        "skills",
        "conversations_search",
        "import_conversation",
        "read_file",
        "workboard",
        "discover_tools",
    } <= names
