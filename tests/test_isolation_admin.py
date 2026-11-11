"""``beakr org isolation``, ``beakr org read-trail`` and ``beakr projects guard``.

What each promises, one test or more each:

- ``show`` prints the settings (a table, compact lines when piped, ``--json``)
  and says which parts are not turned on;
- ``set`` sends only the fields given, after reading the settings: a part not
  turned on is refused with the flag to turn on (exit 4), a caller who may not
  change it (``can_change``) is refused (exit 3), and nothing is sent either way;
- a change that loosens a rule asks first, and without a terminal needs ``--yes``;
- every request carries the person's own credential and no ``X-Beakr-Session``,
  even in a shell session or an agent conversation;
- no MCP tool can reach these routes to change them: an agent must never loosen
  its own guard.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli import scope_session
from beakr_cli.main import internal_app as app

API = "https://api.test"
PERSON_KEY = "bk_live_person_key_for_tests"
ORG = "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"
PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
ISOLATION = f"{API}/v1/organizations/{ORG}/project-isolation"
READ_TRAIL = f"{API}/v1/organizations/{ORG}/read-trail"
GUARD = f"{API}/v1/projects/{PROJECT}/cross-project-guard"

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", PERSON_KEY)
    monkeypatch.setattr("beakr_cli.project_binding.credential_org_id", lambda: ORG)


def _isolation(**overrides) -> dict:
    available = {
        "server_agent_sessions": True,
        "session_auto_lock": True,
        "agent_writes_require_session": True,
        "cross_project_guard": True,
        **overrides.pop("available", {}),
    }
    return {
        "session_mode": "picker",
        "auto_lock_counts_search_results": True,
        "agent_writes_require_session": True,
        "cross_project_guard": "hold",
        "available": available,
        "can_change": True,
        **overrides,
    }


def _read_trail(**overrides) -> dict:
    return {"read_trail_retention_days": 90, "available": True, "can_change": True, **overrides}


def _guard(**overrides) -> dict:
    return {
        "project_id": PROJECT,
        "project_name": "Pharma A",
        "override": None,
        "org_default": "hold",
        "effective": "hold",
        "available": True,
        "can_change": True,
        **overrides,
    }


def _body(route: respx.Route) -> dict:
    return json.loads(route.calls.last.request.content)


def _said(result) -> str:
    """The output with the console's line wrapping undone."""
    return " ".join(result.output.split())


def _refusal(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "detail": {"code": code, "message": message},
            "error": {"code": code, "message": message},
        },
    )


# -- org isolation show ------------------------------------------------------------


@respx.mock
def test_isolation_show_prints_each_setting_and_what_is_turned_on():
    respx.get(ISOLATION).respond(
        json=_isolation(available={"cross_project_guard": False}, can_change=False)
    )

    result = runner.invoke(app, ["org", "isolation", "show"])

    assert result.exit_code == 0, result.output
    assert "session_mode\tpicker\tturned_on=yes" in result.stdout
    assert "agent_writes_require_session\ton\tturned_on=yes" in result.stdout
    assert "cross_project_guard\thold\tturned_on=no" in result.stdout
    assert "can_change\tno" in result.stdout
    assert result.stderr.startswith(
        "Not turned on: cross_project_guard. Ask your operator to turn on cross_project_guard "
        "on the Features page."
    )


@respx.mock
def test_isolation_show_json_is_the_apis_answer():
    respx.get(ISOLATION).respond(json=_isolation())

    result = runner.invoke(app, ["org", "isolation", "show", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _isolation()


@respx.mock
def test_isolation_show_takes_an_org():
    other = "9d8e4c1a-6b2f-4a3e-8c7d-5e0f1a2b3c4d"
    route = respx.get(f"{API}/v1/organizations/{other}/project-isolation").respond(
        json=_isolation()
    )

    result = runner.invoke(app, ["org", "isolation", "show", "--org", other])

    assert result.exit_code == 0, result.output
    assert route.called


# -- org isolation set -------------------------------------------------------------


@respx.mock
def test_isolation_set_sends_only_the_fields_given():
    respx.get(ISOLATION).respond(json=_isolation())
    route = respx.put(ISOLATION).respond(json=_isolation(session_mode="auto_lock"))

    result = runner.invoke(
        app, ["org", "isolation", "set", "--session-mode", "auto_lock", "--guard", "refuse"]
    )

    assert result.exit_code == 0, result.output
    assert _body(route) == {"session_mode": "auto_lock", "cross_project_guard": "refuse"}
    assert "session_mode\tauto_lock" in result.stdout


@respx.mock
def test_isolation_set_tightening_does_not_ask():
    respx.get(ISOLATION).respond(
        json=_isolation(agent_writes_require_session=False, auto_lock_counts_search_results=False)
    )
    route = respx.put(ISOLATION).respond(json=_isolation())

    result = runner.invoke(
        app, ["org", "isolation", "set", "--require-session", "--count-search-results"]
    )

    assert result.exit_code == 0, result.output
    assert _body(route) == {
        "agent_writes_require_session": True,
        "auto_lock_counts_search_results": True,
    }


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["--guard", "allow"], "cross-project guard hold -> allow"),
        (["--guard", "allow"], "Loosen project isolation"),
        (["--no-require-session"], "agent writes need a session on -> off"),
        (["--no-count-search-results"], "search results count as read on -> off"),
    ],
)
@respx.mock
def test_isolation_set_loosening_needs_yes_without_a_terminal(argv, said):
    respx.get(ISOLATION).respond(json=_isolation())
    route = respx.put(ISOLATION).respond(json=_isolation())

    refused = runner.invoke(app, ["org", "isolation", "set", *argv])

    assert refused.exit_code == 2, refused.output
    assert said in _said(refused)
    assert not route.called

    confirmed = runner.invoke(app, ["org", "isolation", "set", *argv, "--yes"])

    assert confirmed.exit_code == 0, confirmed.output
    assert route.call_count == 1


@respx.mock
def test_isolation_set_loosening_asks_at_a_terminal(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    respx.get(ISOLATION).respond(json=_isolation(cross_project_guard="refuse"))
    route = respx.put(ISOLATION).respond(json=_isolation(cross_project_guard="allow"))

    declined = runner.invoke(app, ["org", "isolation", "set", "--guard", "hold"], input="n\n")

    assert declined.exit_code == 1, declined.output
    assert "cross-project guard refuse -> hold" in _said(declined)
    assert not route.called

    accepted = runner.invoke(app, ["org", "isolation", "set", "--guard", "hold"], input="y\n")

    assert accepted.exit_code == 0, accepted.output
    assert _body(route) == {"cross_project_guard": "hold"}


@respx.mock
def test_isolation_set_refuses_a_part_not_turned_on():
    respx.get(ISOLATION).respond(json=_isolation(available={"cross_project_guard": False}))
    route = respx.put(ISOLATION)

    result = runner.invoke(app, ["org", "isolation", "set", "--guard", "refuse"])

    assert result.exit_code == 4, result.output
    assert "Ask your operator to turn on cross_project_guard on the Features page" in _said(result)
    assert not route.called


@respx.mock
def test_isolation_set_refusal_in_json_names_the_code_and_fix():
    respx.get(ISOLATION).respond(json=_isolation(available={"session_auto_lock": False}))

    result = runner.invoke(
        app, ["org", "isolation", "set", "--session-mode", "auto_lock", "--json"]
    )

    assert result.exit_code == 4, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "feature_not_enabled"
    assert error["fix"] == "Ask your operator to turn on session_auto_lock on the Features page."


@respx.mock
def test_isolation_set_refuses_a_caller_who_cannot_change_it():
    respx.get(ISOLATION).respond(json=_isolation(can_change=False))
    route = respx.put(ISOLATION)

    result = runner.invoke(app, ["org", "isolation", "set", "--guard", "allow", "--yes"])

    assert result.exit_code == 3, result.output
    assert "You can't change project isolation settings" in _said(result)
    assert "it needs an organization admin, acting as a person" in _said(result)
    assert not route.called


@respx.mock
def test_isolation_set_says_the_operator_fix_when_the_api_refuses_the_feature():
    respx.get(ISOLATION).respond(json=_isolation())
    respx.put(ISOLATION).mock(
        return_value=_refusal(
            404, "feature_not_enabled", "Not enabled for this organization: cross_project_guard."
        )
    )

    result = runner.invoke(app, ["org", "isolation", "set", "--guard", "refuse"])

    assert result.exit_code == 4, result.output
    assert "Ask your operator to turn on cross_project_guard on the Features page" in _said(result)


@respx.mock
def test_isolation_set_a_credential_that_is_not_a_person_exits_3():
    respx.get(ISOLATION).respond(json=_isolation())
    respx.put(ISOLATION).mock(
        return_value=_refusal(403, "session_required", "Changing these needs a person.")
    )

    result = runner.invoke(app, ["org", "isolation", "set", "--guard", "refuse"])

    assert result.exit_code == 3, result.output
    assert "needs a person" in _said(result)


def test_isolation_set_without_a_field_is_a_usage_error():
    result = runner.invoke(app, ["org", "isolation", "set"])

    assert result.exit_code == 2, result.output
    assert "Give a setting to change" in result.output


# -- the person's own credential -----------------------------------------------------


@respx.mock
def test_requests_carry_the_persons_key_and_no_session(monkeypatch):
    # In a shell session (whose token this machine does not even hold) and an
    # agent conversation: neither the session's token nor the conversation joins.
    monkeypatch.setenv(scope_session.SESSION_ENV, "sess-shell")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-123")
    read = respx.get(ISOLATION).respond(json=_isolation())
    write = respx.put(ISOLATION).respond(json=_isolation())

    result = runner.invoke(app, ["org", "isolation", "set", "--guard", "refuse"])

    assert result.exit_code == 0, result.output
    for route in (read, write):
        request = route.calls.last.request
        assert request.headers["Authorization"] == f"Bearer {PERSON_KEY}"
        assert "X-Beakr-Session" not in request.headers


@pytest.mark.real_conversation_sessions
@respx.mock
def test_an_auto_lock_conversation_does_not_lend_its_session_token(monkeypatch):
    # In an auto_lock conversation the CLI's reads carry the conversation's
    # session token; these commands never do, so no session is started for them.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-123")
    sessions = respx.post(f"{API}/v1/sessions")
    read = respx.get(ISOLATION).respond(json=_isolation(session_mode="auto_lock"))

    result = runner.invoke(app, ["org", "isolation", "show"])

    assert result.exit_code == 0, result.output
    assert not sessions.called
    assert read.calls.last.request.headers["Authorization"] == f"Bearer {PERSON_KEY}"


# -- org read-trail ------------------------------------------------------------------


@respx.mock
def test_read_trail_show():
    respx.get(READ_TRAIL).respond(json=_read_trail(available=False))

    result = runner.invoke(app, ["org", "read-trail", "show"])

    assert result.exit_code == 0, result.output
    assert "read_trail_retention_days\t90" in result.stdout
    assert "turned_on\tno" in result.stdout
    assert "Ask your operator to turn on session_read_trail on the Features page" in (result.stderr)


@respx.mock
def test_read_trail_set_longer_does_not_ask():
    respx.get(READ_TRAIL).respond(json=_read_trail())
    route = respx.put(READ_TRAIL).respond(json=_read_trail(read_trail_retention_days=365))

    result = runner.invoke(app, ["org", "read-trail", "set", "--days", "365", "--json"])

    assert result.exit_code == 0, result.output
    assert _body(route) == {"read_trail_retention_days": 365}
    assert json.loads(result.stdout)["read_trail_retention_days"] == 365


@respx.mock
def test_read_trail_set_shorter_asks_first():
    respx.get(READ_TRAIL).respond(json=_read_trail())
    route = respx.put(READ_TRAIL).respond(json=_read_trail(read_trail_retention_days=7))

    refused = runner.invoke(app, ["org", "read-trail", "set", "--days", "7"])

    assert refused.exit_code == 2, refused.output
    assert "deleted within the hour" in _said(refused)
    assert not route.called

    confirmed = runner.invoke(app, ["org", "read-trail", "set", "--days", "7", "--yes"])

    assert confirmed.exit_code == 0, confirmed.output
    assert _body(route) == {"read_trail_retention_days": 7}


@pytest.mark.parametrize(
    ("settings", "code", "said"),
    [
        (_read_trail(available=False), 4, "turn on session_read_trail on the Features page"),
        (_read_trail(can_change=False), 3, "You can't change read trail retention"),
    ],
)
@respx.mock
def test_read_trail_set_refusals(settings, code, said):
    respx.get(READ_TRAIL).respond(json=settings)
    route = respx.put(READ_TRAIL)

    result = runner.invoke(app, ["org", "read-trail", "set", "--days", "120"])

    assert result.exit_code == code, result.output
    assert said in _said(result)
    assert not route.called


def test_read_trail_set_checks_the_range():
    result = runner.invoke(app, ["org", "read-trail", "set", "--days", "0"])

    assert result.exit_code == 2, result.output


# -- projects guard ------------------------------------------------------------------


@respx.mock
def test_guard_show():
    respx.get(GUARD).respond(json=_guard())

    result = runner.invoke(app, ["projects", "guard", "show", PROJECT])

    assert result.exit_code == 0, result.output
    assert f"project\t{PROJECT}\tPharma A" in result.stdout
    assert "effective\thold" in result.stdout
    assert "override\tdefault" in result.stdout


@pytest.mark.real_project_refs
@respx.mock
def test_guard_resolves_a_project_name_with_the_persons_key(monkeypatch):
    monkeypatch.setenv(scope_session.SESSION_ENV, "sess-shell")
    projects = respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": PROJECT, "name": "Pharma A", "slug": "pharma-a"}]}
    )
    respx.get(GUARD).respond(json=_guard())

    result = runner.invoke(app, ["projects", "guard", "show", "Pharma A", "--json"])

    assert result.exit_code == 0, result.output
    assert projects.calls.last.request.headers["Authorization"] == f"Bearer {PERSON_KEY}"
    assert json.loads(result.stdout)["project_id"] == PROJECT


@pytest.mark.parametrize(
    ("mode", "sent"), [("refuse", "refuse"), ("hold", "hold"), ("default", None)]
)
@respx.mock
def test_guard_set_sends_the_override(mode, sent):
    respx.get(GUARD).respond(json=_guard())
    route = respx.put(GUARD).respond(json=_guard(override=sent))

    result = runner.invoke(app, ["projects", "guard", "set", PROJECT, mode])

    assert result.exit_code == 0, result.output
    assert _body(route) == {"override": sent}


@pytest.mark.parametrize(
    ("current", "mode", "said"),
    [
        (_guard(), "allow", "Pharma A's cross-project guard hold -> allow"),
        # Back to an organization default that is looser than the project's own.
        (
            _guard(override="refuse", effective="refuse", org_default="allow"),
            "default",
            "Pharma A's cross-project guard refuse -> allow",
        ),
    ],
)
@respx.mock
def test_guard_set_loosening_asks_first(current, mode, said):
    respx.get(GUARD).respond(json=current)
    route = respx.put(GUARD).respond(json=_guard(override="allow", effective="allow"))

    refused = runner.invoke(app, ["projects", "guard", "set", PROJECT, mode])

    assert refused.exit_code == 2, refused.output
    assert said in _said(refused)
    assert not route.called

    confirmed = runner.invoke(app, ["projects", "guard", "set", PROJECT, mode, "-y"])

    assert confirmed.exit_code == 0, confirmed.output
    assert route.called


@pytest.mark.parametrize(
    ("current", "code", "said"),
    [
        (_guard(available=False), 4, "turn on cross_project_guard on the Features page"),
        (_guard(can_change=False), 3, "an owner or admin of the project"),
    ],
)
@respx.mock
def test_guard_set_refusals(current, code, said):
    respx.get(GUARD).respond(json=current)
    route = respx.put(GUARD)

    result = runner.invoke(app, ["projects", "guard", "set", PROJECT, "refuse"])

    assert result.exit_code == code, result.output
    assert said in _said(result)
    assert not route.called


def test_guard_set_takes_only_a_guard_or_default():
    result = runner.invoke(app, ["projects", "guard", "set", PROJECT, "off"])

    assert result.exit_code == 2, result.output


# -- never an MCP tool ---------------------------------------------------------------

#: What the routes' paths contain; the MCP server's code must never name them to write.
ROUTE_FRAGMENTS = ("/project-isolation", "/cross-project-guard", "/read-trail")
#: The settings' request fields: no MCP tool takes one.
SETTING_FIELDS = (
    "session_mode",
    "auto_lock_counts_search_results",
    "agent_writes_require_session",
    "cross_project_guard",
    "read_trail_retention_days",
)


def _source(module: str) -> Path | None:
    spec = importlib.util.find_spec(module)
    origin = spec.origin if spec else None
    return Path(origin) if origin and origin.endswith(".py") else None


def _imported(module: str, path: Path, tree: ast.AST) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parts, base] if base else parts)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return {name for name in found if name.startswith("beakr_cli")}


def _mcp_server_modules() -> dict[str, ast.AST]:
    """Every beakr_cli module the MCP server can run, imports inside functions included."""
    modules: dict[str, ast.AST] = {}
    todo = ["beakr_cli.mcp_server"]
    while todo:
        module = todo.pop()
        if module in modules:
            continue
        try:
            path = _source(module)
        except ModuleNotFoundError:
            path = None  # ``from package import function``: not a module
        if path is None:
            continue
        modules[module] = tree = ast.parse(path.read_text())
        todo.extend(_imported(module, path, tree))
    return modules


def _strings(tree: ast.AST) -> list[str]:
    """The module's string literals (f-strings' literal parts too), docstrings left out."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_mcp_tool_can_change_isolation_settings():
    modules = _mcp_server_modules()
    assert "beakr_cli.mcp_server" in modules and "beakr_cli.sdk" in modules
    # The commands are out of the MCP server's reach...
    assert "beakr_cli.commands.isolation_admin" not in modules
    # ...and its own code names the routes only to read the organization's mode
    # (scope_session.isolation_path, sent only with GET).
    named = {
        (module, value)
        for module, tree in modules.items()
        for value in _strings(tree)
        if any(fragment in value for fragment in ROUTE_FRAGMENTS)
        # A pattern (beakr_cli.capabilities) names the area of a 404; it sends nothing.
        and not value.startswith("^")
    }
    assert named == {("beakr_cli.scope_session", "/project-isolation")}, named
    for module, tree in modules.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            target = node.args[1]
            callee = target.func if isinstance(target, ast.Call) else None
            name = getattr(callee, "id", None) or getattr(callee, "attr", None)
            if name == "isolation_path":
                method = node.args[0]
                assert isinstance(method, ast.Constant) and method.value == "GET", (
                    f"{module}:{node.lineno} sends isolation_path with {ast.unparse(method)}"
                )


def test_no_mcp_tool_takes_an_isolation_setting():
    from mcp.server.fastmcp import FastMCP

    from beakr_cli import tool_surface
    from beakr_cli.mcp_server import mcp

    tools = asyncio.run(FastMCP.list_tools(mcp))
    assert tools
    for tool in tools:
        schema = json.dumps(tool.inputSchema)
        for field in SETTING_FIELDS:
            assert f'"{field}"' not in schema, (tool.name, field)
        assert not any(word in tool.name for word in ("isolation", "guard", "read_trail"))
    # Nor does any tool of the engine's shared surface this package vendors.
    surface = json.dumps(tool_surface.document())
    for fragment in ROUTE_FRAGMENTS:
        assert fragment not in surface
