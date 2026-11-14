"""Confirm before entering a locked project (beakr-engine ``max/lock-confirm``).

An automatic session that already worked in another project is held on its first
touch of a locked project: ``409 session_lock_confirm``, nothing read or written.

- ``beakr mcp`` lists ``confirm_lock`` on exactly the shared surface's tools, with
  the surface's property; takes it off the call (top level or inside ``arguments``)
  before the tool's schema checks it; sends it as ``X-Beakr-Confirm-Lock`` on that
  call's requests only; refuses anything but project ids in the hosted server's
  words; and answers a held call as the hosted server does, never asking.
  ``read_file`` and ``upload_files`` answer the hold too, not their own 409 or
  per-file failure.
- A CLI command at a terminal asks "Enter Partner A? ..." and on yes sends the
  request once more with the header; no ends it (exit 1). Without a terminal it
  exits 3 with the API's warning and the command to repeat it with,
  ``beakr --confirm-lock <id> ...``; that option (or ``BEAKR_CONFIRM_LOCK``) sends the
  id on every request, and anything but project ids is a usage error.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import lock_confirm, mcp_server, scope_session, tool_surface
from beakr_cli.main import internal_app as app
from tests.test_project_boundaries import _active, _session

API = "https://api.test"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "56100fcd-8dcf-4844-81cd-f19852340430"
COMMAND = f"{API}/v1/knowledge/wiki/command"
PROJECTS = f"{API}/v1/projects"
COST = "can no longer write to Chemistry (written earlier in this session)"
MESSAGE = (
    "Heads-up: Partner A is siloed. Reading it limits this session's writes to Partner A, "
    "so it can no longer write to Chemistry (written earlier in this session). To go in, "
    f'repeat this call with confirm_lock="{A}"; or ask the user, or start a new session '
    "for Partner A."
)
FIX = f'Repeat this call with confirm_lock="{A}" (over HTTP, send X-Beakr-Confirm-Lock: {A}).'
INVALID_FIX = (
    "Pass the project id a session_lock_confirm answer named, as confirm_lock, or leave it out."
)
#: The engine's tools (docs/api/agent-tool-surface.json ``confirm_lock``), as of 370f67a.
CONFIRM_LOCK_TOOLS = {
    "context",
    "knowledge_base",
    "knowledge_base_write",
    "read_file",
    "research",
    "workboard",
    "documents",
    "get_profile",
    "show_proposal",
    "accept_proposal",
    "dismiss_proposal",
    "upload_files",
    "set_project_profile",
    "add_project_member",
}

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")
    monkeypatch.delenv(lock_confirm.ENV, raising=False)
    monkeypatch.delenv(scope_session.SESSION_ENV, raising=False)
    monkeypatch.delenv(scope_session.MCP_SESSIONS_ENV, raising=False)
    scope_session.ACTIVE = None
    lock_confirm._DECLARED.clear()
    lock_confirm._ASKS[0] = True
    yield
    scope_session.ACTIVE = None
    lock_confirm._DECLARED.clear()
    lock_confirm._ASKS[0] = True


def _held(cost: str = COST, message: str = MESSAGE) -> httpx.Response:
    extras = {
        "session_id": "s1",
        "project": {"id": A, "name": "Partner A"},
        "cost": cost,
        "confirm_lock": A,
    }
    body = {"code": "session_lock_confirm", "message": message, "fix": FIX, **extras}
    return httpx.Response(409, json={"detail": body, "error": dict(body)})


def _held_until_confirmed(ok: httpx.Response):
    def answer(request: httpx.Request) -> httpx.Response:
        return ok if request.headers.get(lock_confirm.HEADER) == A else _held()

    return answer


def _projects_page() -> httpx.Response:
    return httpx.Response(200, json={"data": [], "has_more": False})


# --- beakr mcp ---------------------------------------------------------------------------


def test_the_vendored_surface_names_the_engines_tools_and_property():
    document = tool_surface.document()
    assert set(document["confirm_lock"]) == CONFIRM_LOCK_TOOLS
    assert document["confirm_lock_property"] == {
        "type": "string",
        "description": (
            "The id of a locked project a session_lock_confirm answer named, to enter it. "
            "Pass it only to repeat that call."
        ),
    }


def test_confirm_lock_is_listed_on_exactly_the_surfaces_tools():
    _active(_session("automatic"))
    tools = asyncio.run(mcp_server.mcp.list_tools())
    listed = {tool.name: tool.inputSchema for tool in tools}
    with_it = {name for name, schema in listed.items() if "confirm_lock" in schema["properties"]}
    assert with_it == CONFIRM_LOCK_TOOLS & set(listed)
    # Every one of them is served here, as on the hosted server.
    assert CONFIRM_LOCK_TOOLS <= set(listed)
    for name in with_it:
        schema = listed[name]
        assert (
            schema["properties"]["confirm_lock"] == tool_surface.document()["confirm_lock_property"]
        )
        assert "confirm_lock" not in (schema.get("required") or [])


def test_discover_tools_lists_it_too():
    from beakr_cli.mcp_tools_platform import discover_tools

    found = asyncio.run(discover_tools("proposal"))
    schemas = {tool["name"]: tool["input_schema"] for tool in found["tools"]}
    assert "confirm_lock" in schemas["show_proposal"]["properties"]


def test_take_reads_the_top_level_or_the_arguments_envelope():
    tools = tool_surface.confirm_lock_tools()
    top, ids = lock_confirm.take("knowledge_base", {"command": "ls", "confirm_lock": A}, tools)
    assert top == {"command": "ls"} and ids == (A,)
    nested, ids = lock_confirm.take(
        "knowledge_base", {"command": "ls", "arguments": {"confirm_lock": A.upper()}}, tools
    )
    assert nested == {"command": "ls", "arguments": {}} and ids == (A,)
    _, ids = lock_confirm.take("read_file", {"confirm_lock": f"{A}, {B}, {A}"}, tools)
    assert ids == (A, B)
    assert lock_confirm.take("read_file", {"confirm_lock": ""}, tools)[1] == ()
    # A tool that does not list it leaves it for its own schema to refuse.
    untouched, ids = lock_confirm.take("list_projects", {"confirm_lock": A}, tools)
    assert untouched == {"confirm_lock": A} and ids == ()


@pytest.mark.parametrize(
    ("value", "shown"), [("true", "'true'"), (True, "'True'"), ("Partner A", "'Partner A'")]
)
@respx.mock
def test_anything_but_project_ids_is_refused_in_the_hosted_servers_words(value, shown):
    _active(_session("automatic"))
    door = respx.post(COMMAND)

    result = asyncio.run(
        mcp_server.mcp.call_tool(
            "knowledge_base", {"command": "ls", "scope": A, "confirm_lock": value}
        )
    )

    assert result.isError and not door.called
    assert result.content[0].text == json.dumps(
        {
            "status": "error",
            "error_code": "invalid_arguments",
            "message": f"confirm_lock takes the id of the project to enter; {shown} is not one.",
            "fix": INVALID_FIX,
        },
        separators=(", ", ": "),
    )


@respx.mock
def test_confirm_lock_is_sent_on_that_calls_requests_only():
    _active(_session("automatic"))
    door = respx.post(COMMAND).respond(
        json={"content": [{"type": "text", "text": "No pages."}], "outcome": {"status": "success"}}
    )

    asyncio.run(
        mcp_server.mcp.call_tool("knowledge_base", {"command": "ls", "scope": A, "confirm_lock": A})
    )
    asyncio.run(mcp_server.mcp.call_tool("knowledge_base", {"command": "ls", "scope": A}))

    sent = [call.request.headers.get(lock_confirm.HEADER) for call in door.calls]
    assert sent == [A, None]
    assert "confirm_lock" not in json.loads(door.calls[0].request.content)


@respx.mock
def test_a_held_call_answers_the_compact_session_error_and_never_asks(monkeypatch):
    lock_confirm.serve_without_asking()
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    _active(_session("automatic"))
    door = respx.post(COMMAND).mock(return_value=_held())

    result = asyncio.run(mcp_server.mcp.call_tool("knowledge_base", {"command": "ls", "scope": A}))

    assert result.isError and door.call_count == 1
    assert json.loads(result.content[0].text) == {
        "status": "error",
        "error_code": "session_lock_confirm",
        "message": MESSAGE,
        "fix": FIX,
    }


@respx.mock
def test_read_file_answers_the_hold_not_a_read_conflict():
    _active(_session("automatic"))
    held = _held().json()
    held["error"]["request_id"] = "req-1"
    respx.post(f"{API}/v1/artifacts/read").mock(return_value=httpx.Response(409, json=held))

    result = asyncio.run(
        mcp_server.mcp.call_tool("read_file", {"artifact_id": "notes.md", "project": A})
    )

    assert result.isError
    assert json.loads(result.content[0].text) == {
        "status": "error",
        "error_code": "session_lock_confirm",
        "message": MESSAGE,
        "fix": FIX,
        # As the hosted server sends it (render.error_text); a read conflict drops it.
        "request_id": "req-1",
    }


def test_upload_files_raises_the_hold_instead_of_listing_it_as_a_failed_file():
    import inspect

    from beakr_cli import mcp_tools_projects

    # The per-file loop lets the hold through before it records a failure.
    source = inspect.getsource(mcp_tools_projects)
    loop = source[source.index("except ApiCallError as exc:\n                if exc.fields") :]
    assert loop.index('"session_lock_confirm"') < loop.index("upload.failed.append")


def test_the_instructions_say_how_a_held_call_goes_in():
    text = mcp_server.instructions()
    assert (
        "In an automatic session that already worked in another project, the first call that "
        "touches a locked project is held with a one-line warning (session_lock_confirm) and "
        'nothing is read or written: to go in, repeat the call with confirm_lock="<the project '
        'id it names>", or ask the user. When a write is refused,'
    ) in text


# --- a CLI command -----------------------------------------------------------------------


@respx.mock
def test_without_a_terminal_a_held_command_says_how_to_repeat_it():
    projects = respx.get(PROJECTS).mock(return_value=_held())

    result = runner.invoke(app, ["projects", "ls"])

    assert result.exit_code == 3, result.output
    said = " ".join(result.stderr.split())
    assert said.startswith(
        "Error: Heads-up: Partner A is siloed. Reading it limits this session's writes to "
        "Partner A, so it can no longer write to Chemistry (written earlier in this session). "
        "Nothing was read or written."
    )
    assert f"run it again as `beakr --confirm-lock {A} projects ls ...`" in said
    assert "start a new session for Partner A." in said
    assert "confirm_lock=" not in said
    assert projects.call_count == 1


@respx.mock
def test_confirm_lock_sends_the_id_on_every_request():
    projects = respx.get(PROJECTS).mock(side_effect=_held_until_confirmed(_projects_page()))

    result = runner.invoke(app, ["--confirm-lock", A, "projects", "ls"])

    assert result.exit_code == 0, result.output
    assert [call.request.headers.get(lock_confirm.HEADER) for call in projects.calls] == [A]


@respx.mock
def test_the_environment_confirms_too(monkeypatch):
    monkeypatch.setenv(lock_confirm.ENV, A.upper())
    projects = respx.get(PROJECTS).mock(side_effect=_held_until_confirmed(_projects_page()))

    result = runner.invoke(app, ["projects", "ls"])

    assert result.exit_code == 0, result.output
    assert projects.calls[0].request.headers.get(lock_confirm.HEADER) == A


def test_confirm_lock_takes_project_ids_only():
    result = runner.invoke(app, ["--confirm-lock", "Partner A", "projects", "ls"])

    assert result.exit_code == 2
    said = " ".join(result.output.replace("│", " ").split())
    assert "--confirm-lock takes the id of the project to enter; 'Partner A' is not one." in said


@respx.mock
def test_at_a_terminal_yes_sends_it_once_more_with_the_id(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    projects = respx.get(PROJECTS).mock(side_effect=_held_until_confirmed(_projects_page()))

    result = runner.invoke(app, ["projects", "ls"], input="y\n")

    assert result.exit_code == 0, result.output
    assert (
        "Enter Partner A? This session will only be able to write to Partner A. [y/N]"
        in result.stderr
    )
    assert [call.request.headers.get(lock_confirm.HEADER) for call in projects.calls] == [None, A]


@respx.mock
def test_at_a_terminal_no_ends_the_command(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    projects = respx.get(PROJECTS).mock(return_value=_held())

    result = runner.invoke(app, ["projects", "ls"], input="n\n")

    assert result.exit_code == 1
    assert "Not entering Partner A: nothing was read or written." in result.stderr
    assert projects.call_count == 1


def test_a_second_locked_project_asks_about_read_only():
    error = _sdk_error(
        _held(
            cost=(
                "becomes read-only, because it already read Partner B, which also keeps its "
                "content in"
            )
        )
    )
    assert lock_confirm.prompt_text(error) == (
        "Enter Partner A? This session will become read-only: it will not be able to write."
    )


def test_inside_beakr_mcp_nothing_asks(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    lock_confirm.serve_without_asking()
    assert lock_confirm.entered_after(_sdk_error(_held())) is False


def test_the_sdk_error_carries_the_code():
    error = _sdk_error(_held())
    assert lock_confirm.held(error)
    assert lock_confirm.held_project(error) == (A, "Partner A")


def _sdk_error(response: httpx.Response):
    from beakr_sdk.errors import error_from_response

    response.request = httpx.Request("GET", PROJECTS)
    return error_from_response(response)
