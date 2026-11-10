"""Session project scopes in the CLI and its MCP server (beakr-engine#2442).

What each piece promises, one test or more each:

- ``beakr mcp`` starts a session with the person's credential, makes every tool
  call with the session's token, renews the token with the person's credential
  before it expires, and runs as before on an installation without sessions;
- until the person chooses, a tool answers with the picker link, or offers the
  in-chat form where the client supports form elicitation;
- ``session_scope`` only ever narrows: a project the session does not have is
  refused here, without a request, and the API's own refusal is surfaced;
- a selection the person asked to remember is kept in the folder's binding and
  reused by the next session started there;
- ``beakr session start`` puts a shell in a session by id; commands there send
  the session's token, the session commands themselves the person's credential.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import mcp_session, scope_session
from beakr_cli.main import internal_app as app
from beakr_cli.scope_session import McpSession, SessionScopeError, TokenState

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
C = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
PERSON_KEY = "bk_live_person_key_for_tests"

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)
    monkeypatch.delenv(scope_session.MCP_SESSIONS_ENV, raising=False)
    scope_session.ACTIVE = None
    yield
    scope_session.ACTIVE = None


def _later(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _session(status: str = "active", *, read=(A, B), write=(A,), **extra) -> dict:
    names = {A: "Helios", B: "Assays", C: "Other"}
    return {
        "id": SID,
        "status": status,
        "client_name": "beakr mcp",
        "label": "beakr mcp",
        "folder": extra.pop("folder", None),
        "remember_folder": extra.pop("remember_folder", False),
        "read_projects": [{"id": p, "name": names[p]} for p in read],
        "write_projects": [{"id": p, "name": names[p]} for p in write],
        "picker_url": f"https://app.test/sessions/{SID}",
        "run_label": f"session:{SID}",
        **extra,
    }


def _created(status: str = "pending", **kw) -> dict:
    return {
        **_session(status, **kw),
        "token": {"token": "bk_st_first", "token_type": "bearer", "expires_at": _later()},
    }


def _bearer(route) -> str:
    return route.calls.last.request.headers["authorization"]


def _active(session: dict, *, token: str = "bk_st_first", expires: str | None = None) -> McpSession:
    held = McpSession(folder=None)
    held.state = TokenState(SID, token, expires or _later())
    held.session = session
    scope_session.ACTIVE = held
    return held


# --- narrowing never widens ---------------------------------------------------------


def test_narrowed_drops_projects_by_id_or_name():
    session = _session(read=(A, B), write=(A, B))
    assert scope_session.narrowed(session, drop=["Assays"]) == ([A], [A])
    assert scope_session.narrowed(session, drop_write=[A]) == ([A, B], [B])


def test_narrowed_refuses_a_project_the_session_does_not_have():
    with pytest.raises(SessionScopeError) as refused:
        scope_session.narrowed(_session(read=(A,), write=()), drop=[C])
    assert refused.value.code == scope_session.WIDEN_REFUSED
    # drop_write names only write projects: a read-only project is not one.
    with pytest.raises(SessionScopeError):
        scope_session.narrowed(_session(read=(A, B), write=(A,)), drop_write=[B])


@respx.mock
def test_session_scope_narrows_with_the_session_token():
    _active(_session(read=(A, B), write=(A,)))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session(read=(A, B), write=(A,)))
    patch = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session(read=(A,), write=(A,)))

    text = asyncio.run(mcp_session.session_scope(drop=[B]))

    sent = json.loads(patch.calls.last.request.content)
    assert sent == {"read_project_ids": [A], "write_project_ids": [A]}
    assert _bearer(patch) == "Bearer bk_st_first"
    assert "Reads: Helios" in text


@respx.mock
def test_session_scope_refuses_to_add_a_project_without_asking_the_api():
    _active(_session(read=(A,), write=()))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session(read=(A,), write=()))
    patch = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session())

    result = asyncio.run(mcp_session.session_scope(drop=[C]))

    assert result["error_code"] == "session_widen_refused"
    assert not patch.called


@respx.mock
def test_the_apis_widen_refusal_reaches_the_agent():
    """If the lists the tool computes ever named a new project, the API refuses it."""
    from beakr_cli.mcp_server import mcp

    _active(_session(read=(A, B), write=(A,)))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session(read=(A, B), write=(A,)))
    respx.patch(f"{API}/v1/sessions/{SID}").respond(
        403,
        json={
            "detail": {"code": "session_widen_refused", "message": "only drop"},
            "error": {"code": "session_widen_refused", "message": "only drop", "fix": None},
        },
    )
    result = asyncio.run(mcp.call_tool("session_scope", {"drop": [B]}))
    body = json.loads(result.content[0].text)
    assert result.isError and body["error_code"] == "session_widen_refused"


# --- the server's session -------------------------------------------------------------


@respx.mock
def test_beakr_mcp_starts_a_session_with_the_persons_credential(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    created = respx.post(f"{API}/v1/sessions").respond(201, json=_created())

    held = asyncio.run(scope_session.start_for_mcp())

    assert held is scope_session.ACTIVE and held.token == "bk_st_first" and held.pending
    body = json.loads(created.calls.last.request.content)
    assert body["client_name"] == "beakr mcp" and body["issue_token"] is True
    assert body["folder"] == str(tmp_path)
    assert "read_project_ids" not in body
    assert _bearer(created) == f"Bearer {PERSON_KEY}"


@respx.mock
def test_without_sessions_on_the_installation_beakr_mcp_runs_as_before():
    respx.post(f"{API}/v1/sessions").respond(404, json={"detail": "Not Found"})
    assert asyncio.run(scope_session.start_for_mcp()) is None
    assert scope_session.ACTIVE is None
    from beakr_cli.sdk import get_async_sdk

    assert get_async_sdk()._explicit_api_key is None


def test_sessions_can_be_turned_off_for_the_server(monkeypatch):
    monkeypatch.setenv(scope_session.MCP_SESSIONS_ENV, "off")
    with respx.mock:
        route = respx.post(f"{API}/v1/sessions").respond(201, json=_created())
        assert asyncio.run(scope_session.start_for_mcp()) is None
        assert not route.called


@respx.mock
def test_a_failed_start_is_retried_and_never_falls_back_to_the_persons_reach(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.post(f"{API}/v1/sessions").respond(500, json={"detail": "boom"})
    held = asyncio.run(scope_session.start_for_mcp())
    assert held is scope_session.ACTIVE and held.state is None
    from beakr_cli.mcp_server import mcp

    with respx.mock:
        respx.post(f"{API}/v1/sessions").respond(500, json={"detail": "boom"})
        projects = respx.get(f"{API}/v1/projects").respond(json={"data": []})
        result = asyncio.run(mcp.call_tool("knowledge_base", {"command": "ls"}))
        assert result.isError
        assert json.loads(result.content[0].text)["error_code"] == "session_unavailable"
        assert not projects.called


@respx.mock
def test_tool_calls_carry_the_session_token():
    from beakr_cli.mcp_errors import call_api

    _active(_session())
    route = respx.get(f"{API}/v1/me").respond(json={"user": {}})
    asyncio.run(call_api(lambda sdk: sdk.me.get()))
    assert _bearer(route) == "Bearer bk_st_first"


@respx.mock
def test_the_token_is_renewed_with_the_persons_credential_before_it_expires():
    from beakr_cli.mcp_errors import call_api

    _active(_session(), expires=_later(60))
    renewed = respx.post(f"{API}/v1/sessions/{SID}/tokens").respond(
        201, json={"token": "bk_st_second", "token_type": "bearer", "expires_at": _later()}
    )
    route = respx.get(f"{API}/v1/me").respond(json={"user": {}})
    asyncio.run(call_api(lambda sdk: sdk.me.get()))
    assert _bearer(renewed) == f"Bearer {PERSON_KEY}"
    assert _bearer(route) == "Bearer bk_st_second"


# --- a session the person ended -------------------------------------------------------

SID2 = "2f3e4d5c-6b7a-4c8d-9e0f-1a2b3c4d5e6f"
#: What the API says to a refused session token: the sign-in hint the CLI must not pass on.
_TOKEN_REFUSED = {
    "error": {
        "code": "invalid_token",
        "message": "Invalid, expired or ended session token",
        "fix": "Reconnect the client, or run `beakr auth login`.",
    }
}


def _replacement() -> dict:
    return {
        **_session("pending", read=(), write=()),
        "id": SID2,
        "picker_url": f"https://app.test/sessions/{SID2}",
        "token": {"token": "bk_st_new", "token_type": "bearer", "expires_at": _later()},
    }


def _ended_on_the_web(*, lookup: httpx.Response | None = None):
    """The person ended SID on the web: its token is refused; the person's credential
    sees it ended (or ``lookup``), and a new session can be started."""
    looked = respx.get(f"{API}/v1/sessions/{SID}").mock(
        return_value=lookup or httpx.Response(200, json=_session("ended"))
    )
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_replacement())
    current = respx.get(f"{API}/v1/sessions/current").respond(
        json={k: v for k, v in _replacement().items() if k != "token"}
    )
    return looked, started, current


@respx.mock
def test_an_ended_session_is_replaced_by_a_pending_one(tmp_path):
    from beakr_cli.mcp_errors import call_api

    # A remembered choice is not reused: the person just ended a session here.
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1"})
    scope_session.remember_selection(tmp_path, [A], [A])
    assert scope_session.remembered_selection(tmp_path) == ([A], [A])
    held = _active(_session())
    held.folder = str(tmp_path)
    looked, started, _current = _ended_on_the_web()
    me = respx.get(f"{API}/v1/me").respond(401, json=_TOKEN_REFUSED)

    with pytest.raises(SessionScopeError) as refused:
        asyncio.run(call_api(lambda sdk: sdk.me.get()))

    assert str(refused.value.message) == scope_session.REPLACED_MESSAGE
    assert refused.value.code == scope_session.NEEDS_SELECTION
    assert f"https://app.test/sessions/{SID2}" in refused.value.fix
    assert "auth login" not in f"{refused.value.message} {refused.value.fix}"
    assert _bearer(looked) == f"Bearer {PERSON_KEY}" and _bearer(started) == f"Bearer {PERSON_KEY}"
    body = json.loads(started.calls.last.request.content)
    assert "read_project_ids" not in body and "write_project_ids" not in body
    assert held.id == SID2 and held.token == "bk_st_new" and held.pending
    assert held.replaced_from == SID
    # The refused call is not made again with the new session: it reaches nothing yet.
    assert me.call_count == 1


@respx.mock
def test_a_deleted_session_is_replaced_too():
    from beakr_cli.mcp_errors import call_api

    held = _active(_session())
    _ended_on_the_web(lookup=httpx.Response(404, json={"detail": "Session not found."}))
    respx.get(f"{API}/v1/me").respond(401, json=_TOKEN_REFUSED)

    with pytest.raises(SessionScopeError):
        asyncio.run(call_api(lambda sdk: sdk.me.get()))
    assert held.id == SID2 and held.pending


@respx.mock
def test_after_replacing_it_the_tool_call_answers_with_the_picker():
    from beakr_cli.mcp_server import mcp

    _active(_session())
    _ended_on_the_web()
    respx.route(url__startswith=API).respond(401, json=_TOKEN_REFUSED)

    result = asyncio.run(mcp.call_tool("knowledge_base", {"command": "ls"}))

    assert result.isError
    answer = json.loads(result.content[0].text)
    assert answer["error_code"] == scope_session.NEEDS_SELECTION
    assert answer["message"].startswith(scope_session.REPLACED_MESSAGE)
    assert answer["picker_url"] == f"https://app.test/sessions/{SID2}"
    assert "auth login" not in result.content[0].text
    # Said once: the next call asks as any pending session does.
    again = json.loads(
        asyncio.run(mcp.call_tool("knowledge_base", {"command": "ls"})).content[0].text
    )
    assert again["error_code"] == scope_session.NEEDS_SELECTION
    assert not again["message"].startswith(scope_session.REPLACED_MESSAGE)


@respx.mock
def test_after_replacing_it_the_in_chat_form_says_why_and_the_choice_applies():
    from beakr_cli.mcp_errors import call_api

    held = _active(_session())
    _ended_on_the_web()
    respx.get(f"{API}/v1/me").respond(401, json=_TOKEN_REFUSED)
    with pytest.raises(SessionScopeError):
        asyncio.run(call_api(lambda sdk: sdk.me.get()))
    respx.get(f"{API}/v1/projects").respond(json={"data": [{"id": A, "name": "Helios"}]})
    chosen = respx.patch(f"{API}/v1/sessions/{SID2}").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={**_session(read=(A,), write=()), "id": SID2}
            if b"read_project_ids" in request.content
            else _replacement(),
        )
    )
    server = _server(
        elicitation=SimpleNamespace(form=None, url=None),
        answer=SimpleNamespace(action="accept", content={"read_all": True}),
    )

    refusal = asyncio.run(mcp_session.gate(server, "knowledge_base", None))

    assert refusal is None and not held.pending
    assert server.asked[0].startswith(scope_session.REPLACED_MESSAGE)
    assert json.loads(chosen.calls.last.request.content)["read_project_ids"] == [A]
    assert _bearer(chosen) == f"Bearer {PERSON_KEY}"


@respx.mock
def test_a_pending_session_ended_on_the_web_is_replaced_at_the_gate():
    held = _active(_session("pending", read=(), write=()))
    looked = respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session("ended"))
    respx.post(f"{API}/v1/sessions").respond(201, json=_replacement())
    respx.get(f"{API}/v1/sessions/current").mock(
        side_effect=lambda request: (
            httpx.Response(401, json=_TOKEN_REFUSED)
            if request.headers["authorization"] == "Bearer bk_st_first"
            else httpx.Response(200, json=_replacement())
        )
    )
    respx.patch(f"{API}/v1/sessions/{SID}").respond(401, json=_TOKEN_REFUSED)

    refusal = asyncio.run(mcp_session.gate(_server(), "knowledge_base", "claude-code/2"))

    assert looked.called and held.id == SID2
    assert refusal["message"].startswith(scope_session.REPLACED_MESSAGE)
    assert refusal["picker_url"] == f"https://app.test/sessions/{SID2}"


@respx.mock
def test_an_open_session_whose_token_expired_is_renewed_and_the_call_retried_once():
    from beakr_cli.mcp_errors import call_api

    held = _active(_session())
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    renewed = respx.post(f"{API}/v1/sessions/{SID}/tokens").respond(
        201, json={"token": "bk_st_second", "token_type": "bearer", "expires_at": _later()}
    )
    started = respx.post(f"{API}/v1/sessions").respond(201, json=_replacement())
    me = respx.get(f"{API}/v1/me").mock(
        side_effect=[httpx.Response(401, json=_TOKEN_REFUSED), httpx.Response(200, json={})]
    )

    asyncio.run(call_api(lambda sdk: sdk.me.get()))

    assert me.call_count == 2 and _bearer(me) == "Bearer bk_st_second"
    assert _bearer(renewed) == f"Bearer {PERSON_KEY}"
    assert held.id == SID and held.replaced_from is None and not started.called


@respx.mock
def test_a_token_refused_again_after_renewal_never_says_sign_in():
    from beakr_cli.mcp_errors import call_api

    _active(_session())
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    respx.post(f"{API}/v1/sessions/{SID}/tokens").respond(
        201, json={"token": "bk_st_second", "token_type": "bearer", "expires_at": _later()}
    )
    me = respx.get(f"{API}/v1/me").respond(401, json=_TOKEN_REFUSED)

    with pytest.raises(SessionScopeError) as refused:
        asyncio.run(call_api(lambda sdk: sdk.me.get()))

    assert me.call_count == 2
    assert refused.value.code == scope_session.SESSION_ENDED
    assert "auth login" not in f"{refused.value.message} {refused.value.fix}"


# --- until the person chooses ---------------------------------------------------------


def _server(*, elicitation=None, answer=None):
    async def elicit_form(message, schema):
        server.asked = (message, schema)
        return answer

    capabilities = SimpleNamespace(elicitation=elicitation)
    session = SimpleNamespace(
        client_params=SimpleNamespace(capabilities=capabilities), elicit_form=elicit_form
    )
    server = SimpleNamespace(get_context=lambda: SimpleNamespace(session=session), asked=None)
    return server


@respx.mock
def test_a_pending_session_answers_with_the_picker_link():
    _active(_session("pending", read=(), write=()))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session("pending", read=(), write=()))
    respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session("pending", read=(), write=()))

    refusal = asyncio.run(mcp_session.gate(_server(), "knowledge_base", "claude-code/2"))

    assert refusal["error_code"] == "session_needs_selection"
    assert refusal["picker_url"] == f"https://app.test/sessions/{SID}"
    assert "Show the user this link" in refusal["fix"]


@respx.mock
def test_where_the_client_supports_forms_the_person_chooses_in_chat(tmp_path):
    held = _active(_session("pending", read=(), write=(), folder=str(tmp_path)))
    held.folder = str(tmp_path)
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1"})
    respx.get(f"{API}/v1/sessions/current").respond(json=_session("pending", read=(), write=()))
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": A, "name": "Helios"}, {"id": B, "name": "Assays"}]}
    )
    chosen = respx.patch(f"{API}/v1/sessions/{SID}").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=_session(read=(A, B), write=(B,), remember_folder=True, folder=str(tmp_path))
            if b"read_project_ids" in request.content
            else _session("pending", read=(), write=()),
        )
    )
    answer = SimpleNamespace(
        action="accept", content={"read_0": True, "write_1": True, "remember": True}
    )
    server = _server(elicitation=SimpleNamespace(form=None, url=None), answer=answer)

    refusal = asyncio.run(mcp_session.gate(server, "knowledge_base", None))

    assert refusal is None and not held.pending
    sent = json.loads(chosen.calls.last.request.content)
    # Writing Assays means reading it too; the person's credential applies it.
    assert sent == {"read_project_ids": [A, B], "write_project_ids": [B], "remember_folder": True}
    assert _bearer(chosen) == f"Bearer {PERSON_KEY}"
    assert "Read: Helios" in json.dumps(server.asked[1])
    # Asked to remember: the folder's binding now holds the choice.
    binding = json.loads((tmp_path / ".beakr" / "project.json").read_text())
    assert binding["session_scope"] == {"read_project_ids": [A, B], "write_project_ids": [B]}


def test_a_client_without_form_elicitation_gets_the_link():
    assert not mcp_session.supports_form_elicitation(_server())
    url_only = SimpleNamespace(form=None, url=SimpleNamespace())
    assert not mcp_session.supports_form_elicitation(_server(elicitation=url_only))
    assert mcp_session.supports_form_elicitation(
        _server(elicitation=SimpleNamespace(form=SimpleNamespace(), url=None))
    )


def _many(count: int) -> list[dict]:
    return [{"id": f"00000000-0000-4000-8000-{n:012d}", "name": f"P{n}"} for n in range(count)]


def _form(projects: list[dict], content: dict):
    """Run the gate on a pending session with ``projects``; the form answered ``content``."""
    _active(_session("pending", read=(), write=()))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session("pending", read=(), write=()))
    respx.get(f"{API}/v1/projects").respond(json={"data": projects})
    chosen = respx.patch(f"{API}/v1/sessions/{SID}").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=_session(read=(A,), write=())
            if b"read_project_ids" in request.content
            else _session("pending", read=(), write=()),
        )
    )
    server = _server(
        elicitation=SimpleNamespace(form=None, url=None),
        answer=SimpleNamespace(action="accept", content=content),
    )
    refusal = asyncio.run(mcp_session.gate(server, "knowledge_base", None))
    sent = json.loads(chosen.calls.last.request.content) if chosen.called else None
    return refusal, server.asked, sent


@respx.mock
def test_the_in_chat_form_offers_read_all_and_write_all_first():
    projects = [{"id": A, "name": "Helios"}, {"id": B, "name": "Assays"}]
    refusal, (message, schema), sent = _form(projects, {"write_all": True})

    assert refusal is None
    fields = list(schema["properties"])
    assert fields[:2] == ["read_all", "write_all"] and "read_0" in fields
    assert schema["properties"]["write_all"]["title"] == "Write all projects (also reads them)"
    assert "Tick Read all or Write all, or choose per project below" in message
    assert "created later are not added" in message
    # Write all reads and writes every project the person has now.
    assert sent == {"read_project_ids": [A, B], "write_project_ids": [A, B]}


def test_read_all_keeps_the_per_project_write_ticks():
    fields = {"read_0": A, "write_0": A, "read_1": B, "write_1": B, "read_2": C, "write_2": C}
    every = [A, B, C]
    assert mcp_session.selection_from({"read_all": True, "write_1": True}, fields, every) == (
        [A, B, C],
        [B],
    )
    # Write all overrides the rows; without either, the rows decide.
    assert mcp_session.selection_from({"write_all": True, "read_0": False}, fields, every) == (
        every,
        every,
    )
    assert mcp_session.selection_from({"read_2": True}, fields, every) == ([C], [])


@respx.mock
def test_past_the_row_limit_the_form_offers_only_all():
    projects = _many(mcp_session.FORM_MAX_PROJECTS + 5)
    refusal, (message, schema), sent = _form(projects, {"read_all": True})

    assert refusal is None
    assert list(schema["properties"]) == ["read_all", "write_all"]
    assert f"You have {len(projects)} projects, too many to list here" in message
    assert f"https://app.test/sessions/{SID}" in message
    assert sent["read_project_ids"] == [p["id"] for p in projects]
    assert sent["write_project_ids"] == []


@respx.mock
def test_more_projects_than_a_session_holds_gets_the_link():
    refusal, asked, sent = _form(_many(scope_session.MAX_PROJECTS + 1), {"read_all": True})

    assert asked is None and sent is None
    assert refusal["error_code"] == "session_needs_selection"


def test_session_scope_is_listed_only_while_a_session_is_held(monkeypatch):
    from beakr_cli.mcp_server import mcp

    def names():
        return {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert "session_scope" not in names()
    _active(_session())
    assert "session_scope" in names()


@respx.mock
def test_list_projects_lists_the_sessions_projects():
    from beakr_cli.mcp_server import mcp

    _active(_session(read=(A, B), write=(A,)))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session(read=(A, B), write=(A,)))
    org_list = respx.get(f"{API}/v1/projects").respond(json={"data": []})
    result = asyncio.run(mcp.call_tool("list_projects", {}))
    text = result.content[0].text
    assert "Helios" in text and "read and write" in text and "Assays" in text
    assert not org_list.called


# --- folder memory --------------------------------------------------------------------


def test_a_remembered_selection_starts_the_next_session_in_that_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scope_session.remember_selection(tmp_path, [A, B], [A])
    assert scope_session.remembered_selection(tmp_path) == ([A, B], [A])
    with respx.mock:
        created = respx.post(f"{API}/v1/sessions").respond(201, json=_created("active"))
        asyncio.run(scope_session.start_for_mcp())
    body = json.loads(created.calls.last.request.content)
    assert body["read_project_ids"] == [A, B] and body["write_project_ids"] == [A]
    assert scope_session.forget_selection(tmp_path) is not None
    assert scope_session.remembered_selection(tmp_path) is None


# --- a shell in a session -------------------------------------------------------------


@respx.mock
def test_session_start_puts_a_shell_in_the_session_by_id(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    created = respx.post(f"{API}/v1/sessions").respond(201, json=_created("active"))

    result = runner.invoke(app, ["session", "start", "--read", A, "--write", B, "--env"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"export {scope_session.SESSION_ENV}={SID}"
    sent = json.loads(created.calls.last.request.content)
    assert sent["read_project_ids"] == [A, B] and sent["write_project_ids"] == [B]
    state = scope_session.load_state(SID)
    assert state is not None and state.token == "bk_st_first"
    assert "bk_st_first" not in result.output


@respx.mock
def test_commands_in_a_session_shell_send_its_token(monkeypatch):
    from beakr_cli.sdk import get_sdk

    scope_session.save_state(TokenState(SID, "bk_st_shell", _later()))
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    route = respx.get(f"{API}/v1/me").respond(json={"user": {}})
    with get_sdk() as sdk:
        sdk.me.get()
    assert _bearer(route) == "Bearer bk_st_shell"
    with get_sdk(scoped=False) as sdk:
        sdk.me.get()
    assert _bearer(route) == f"Bearer {PERSON_KEY}"


@respx.mock
def test_an_expiring_shell_token_is_renewed(monkeypatch):
    from beakr_cli.sdk import get_sdk

    scope_session.save_state(TokenState(SID, "bk_st_old", _later(30)))
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    respx.post(f"{API}/v1/sessions/{SID}/tokens").respond(
        201, json={"token": "bk_st_new", "token_type": "bearer", "expires_at": _later()}
    )
    route = respx.get(f"{API}/v1/me").respond(json={"user": {}})
    with get_sdk() as sdk:
        sdk.me.get()
    assert _bearer(route) == "Bearer bk_st_new"
    assert scope_session.load_state(SID).token == "bk_st_new"


def test_a_shell_naming_an_unknown_session_does_not_fall_back(monkeypatch):
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    result = runner.invoke(app, ["projects", "ls"])
    assert result.exit_code != 0
    assert "no token for it here" in result.output


@respx.mock
def test_session_edit_uses_the_persons_credential_inside_the_session(monkeypatch):
    scope_session.save_state(TokenState(SID, "bk_st_shell", _later()))
    monkeypatch.setenv(scope_session.SESSION_ENV, SID)
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    patched = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session(read=(A, C)))
    result = runner.invoke(app, ["session", "edit", "--read", A, "--read", C, "--json"])
    assert result.exit_code == 0, result.output
    assert _bearer(patched) == f"Bearer {PERSON_KEY}"
    assert json.loads(patched.calls.last.request.content)["read_project_ids"] == [A, C]


@respx.mock
def test_session_end_forgets_the_token():
    scope_session.save_state(TokenState(SID, "bk_st_shell", _later()))
    respx.post(f"{API}/v1/sessions/{SID}/end").respond(json=_session("ended"))
    result = runner.invoke(app, ["session", "end", SID])
    assert result.exit_code == 0, result.output
    assert scope_session.load_state(SID) is None


def test_the_terminal_picker_toggles_read_and_write():
    projects = [
        {"id": A, "name": "Helios"},
        {"id": B, "name": "Assays"},
        {"id": C, "name": "Other"},
    ]
    answers = iter(["r 1,3", "w 2", "r 3", ""])
    from beakr_cli.commands.session import pick

    picked = pick(projects, [], [], prompt=lambda _text: next(answers))
    # Writing Assays reads it too; unticking Other's read removed it.
    assert picked == ([A, B], [B])
    assert pick(projects, [A], [], prompt=lambda _text: "q") is None


def test_the_state_file_is_private():
    path = scope_session.save_state(TokenState(SID, "bk_st_secret", _later()))
    assert oct(Path(path).stat().st_mode & 0o777) == "0o600"


def test_the_terminal_picker_ticks_and_clears_every_project():
    projects = [
        {"id": A, "name": "Helios"},
        {"id": B, "name": "Assays"},
        {"id": C, "name": "Other"},
    ]
    from beakr_cli.commands.session import pick

    answers = iter(["r all", "w 2", ""])
    assert pick(projects, [], [], prompt=lambda _text: next(answers)) == ([A, B, C], [B])
    answers = iter(["w all", ""])
    assert pick(projects, [], [], prompt=lambda _text: next(answers)) == ([A, B, C], [A, B, C])
    # `w none` keeps the reads; `r none` clears both (no reads means no writes).
    answers = iter(["w none", ""])
    assert pick(projects, [A, B], [A], prompt=lambda _text: next(answers)) == ([A, B], [])
    answers = iter(["R NONE", "r 3", ""])
    assert pick(projects, [A, B], [A], prompt=lambda _text: next(answers)) == ([C], [])
    asked: list[str] = []
    pick(projects, [], [], prompt=lambda text: asked.append(text) or "")
    assert "`r all` / `w all` tick every current project" in asked[0]


def test_the_terminal_picker_refuses_more_than_a_session_holds(capsys):
    from beakr_cli.commands.session import pick

    projects = [
        {"id": f"00000000-0000-4000-8000-{n:012d}", "name": f"P{n}"}
        for n in range(scope_session.MAX_PROJECTS + 1)
    ]
    answers = iter(["r all", "q"])
    picked = pick(
        projects, [], [], prompt=lambda _text: next(answers), picker_url="https://app.test/s"
    )
    assert picked is None
    said = capsys.readouterr().err
    assert "You can see 201 projects and a session may name at most 200" in said
    assert "https://app.test/s" in said
    # Ticked one by one past the limit, Enter says so instead of accepting.
    every = [p["id"] for p in projects]
    answers = iter(["", "q"])
    assert pick(projects, every, [], prompt=lambda _text: next(answers)) is None
    assert "201 projects are ticked" in capsys.readouterr().err


@respx.mock
def test_session_start_read_all_and_write_all(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": A, "name": "Helios"}, {"id": B, "name": "Assays"}]}
    )
    created = respx.post(f"{API}/v1/sessions").respond(201, json=_created("active"))

    result = runner.invoke(app, ["session", "start", "--read-all", "--write", B, "--env"])
    assert result.exit_code == 0, result.output
    sent = json.loads(created.calls.last.request.content)
    assert sent["read_project_ids"] == [A, B] and sent["write_project_ids"] == [B]

    result = runner.invoke(app, ["session", "start", "--write-all", "--env"])
    assert result.exit_code == 0, result.output
    sent = json.loads(created.calls.last.request.content)
    assert sent["read_project_ids"] == [A, B] and sent["write_project_ids"] == [A, B]


@respx.mock
def test_session_edit_write_all_and_the_limit(monkeypatch):
    respx.get(f"{API}/v1/sessions/{SID}").respond(json=_session())
    patched = respx.patch(f"{API}/v1/sessions/{SID}").respond(json=_session())
    projects = respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": A}, {"id": B}, {"id": C}]}
    )

    result = runner.invoke(app, ["session", "edit", SID, "--write-all", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(patched.calls.last.request.content) == {
        "read_project_ids": [A, B, C],
        "write_project_ids": [A, B, C],
    }

    projects.respond(
        json={
            "data": [
                {"id": f"00000000-0000-4000-8000-{n:012d}"}
                for n in range(scope_session.MAX_PROJECTS + 1)
            ]
        }
    )
    result = runner.invoke(app, ["session", "edit", SID, "--read-all"])
    assert result.exit_code != 0
    assert "201 projects" in result.output and "at most 200" in result.output
    assert f"https://app.test/sessions/{SID}" in result.output
    assert patched.call_count == 1
