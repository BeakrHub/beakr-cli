"""``beakr api``: a command for every public operation, sent through the SDK.

Root cause guarded: the CLI covered only the operations someone had written a
command for (74 of 353 web actions), so the "every web action has a CLI
command" bar of the developer platform could not be met by hand-writing. The
``api`` group is generated from the public contract; these tests keep the table
current with the contract, prove every command is built and actually sends its
operation, and pin the argument mapping (path arguments, query and body
options, ``--data``, multipart files, file and event-stream responses).

``beakr api`` covers the documented contract only. The documented core has no
file download, multipart upload or null answer today, so the command builder's
handling of those is pinned on commands built from operations shaped as the
engine serves them (written out in this file) and invoked directly, outside
``beakr api``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import click.testing
import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.api_manifest import build_manifest
from beakr_cli.commands.api import build_command, command_name, operations
from beakr_cli.main import app
from tests._engine import engine_checkout

API = "https://api.test"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(isolated_home, monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _contract() -> dict:
    return json.loads(engine_checkout("docs/api/public-openapi.json").read_text())


_ERRORS = {"4XX": {"description": "The request was refused."}}


def _uuid_path_param(name: str) -> dict:
    return {"in": "path", "name": name, "required": True, "schema": {"type": "string"}}


def _ok(content: dict) -> dict:
    return {"200": {"description": "OK.", "content": content}, **_ERRORS}


#: Operations shaped as the engine serves them (a file download, a multipart
#: upload, a JSON answer that may be null), none of them in the documented core.
#: Written out here rather than read from the engine's internal contract, which
#: this repository never holds.
_BUILDER_CONTRACT: dict = {
    "openapi": "3.1.0",
    "info": {"title": "command builder cases", "version": "1"},
    "paths": {
        "/v1/artifacts/{artifact_id}/preview/pdf": {
            "get": {
                "operationId": "get_artifact_preview_pdf",
                "summary": "Download a file's preview PDF",
                "tags": ["artifacts"],
                "x-beakr-scope": "projects:read",
                "security": [{"bearer": []}],
                "parameters": [_uuid_path_param("artifact_id")],
                "responses": _ok(
                    {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}
                ),
            }
        },
        "/v1/canvas-templates/{template_id}/logo": {
            "post": {
                "operationId": "upload_logo",
                "summary": "Upload the template's logo",
                "tags": ["canvas-templates"],
                "x-beakr-scope": "outputs:write",
                "security": [{"bearer": []}],
                "parameters": [_uuid_path_param("template_id")],
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {"$ref": "#/components/schemas/Body_upload_logo"}
                        }
                    },
                },
                "responses": _ok({"application/json": {"schema": {"type": "object"}}}),
            }
        },
        "/v1/me/public-profile": {
            "get": {
                "operationId": "get_my_profile",
                "summary": "Return the authenticated user's profile or null if none",
                "tags": ["auth"],
                "x-beakr-scope": "profiles:read",
                "security": [{"bearer": []}],
                "responses": _ok(
                    {
                        "application/json": {
                            "schema": {"anyOf": [{"type": "object"}, {"type": "null"}]}
                        }
                    }
                ),
            }
        },
    },
    "components": {
        "schemas": {
            "Body_upload_logo": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "string",
                        "contentMediaType": "application/octet-stream",
                        "description": "The file.",
                    }
                },
            }
        }
    },
}


def _builder_operations() -> list[dict]:
    """Command-table entries for operations outside ``beakr api``, built as it builds them."""
    return build_manifest(_BUILDER_CONTRACT)["operations"]


def _invoke_built(entry: dict, argv: list[str]):
    """Run the command ``beakr api`` would build for ``entry``; ``argv`` as for ``beakr``."""
    return click.testing.CliRunner().invoke(build_command(entry), argv[3:])


def test_beakr_api_has_no_unlisted_operation():
    spec = _contract()
    documented = {
        f"{method.upper()} {path}"
        for path, item in spec["paths"].items()
        for method in item
        if method in ("get", "post", "put", "patch", "delete")
    }
    assert {entry["op"] for entry in operations()} <= documented


def test_the_vendored_command_table_is_the_contracts():
    from beakr_cli.commands.api import manifest

    assert manifest() == build_manifest(_contract()), (
        "data/operations.json is stale: run scripts/sync_operations.py"
    )


def test_every_public_operation_has_a_command():
    spec = _contract()
    commanded = {entry["op"] for entry in operations()}
    missing = []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            op = f"{method.upper()} {path}"
            if operation.get("deprecated"):
                # A deprecated alias has no command of its own: its canonical
                # operation must have one.
                canonical = operation.get("x-beakr-canonical-path")
                assert canonical and f"{method.upper()} {canonical}" in commanded, op
                continue
            if op not in commanded:
                missing.append(op)
    assert missing == []


def test_command_names_are_unique_and_well_formed():
    names = [command_name(entry) for entry in operations()]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"api [a-z0-9-]+ [a-z0-9-]+", name) for name in names)


def _placeholder(field: dict) -> str:
    if field.get("enum"):
        return field["enum"][0]
    return {"integer": "1", "number": "1", "boolean": "true"}.get(field["type"], "x1")


def _argv(entry: dict, tmp_path: Path) -> list[str]:
    """The smallest valid invocation: placeholders for every required input."""
    argv = ["api", entry["area"], entry["action"]]
    argv += [_placeholder(p) for p in entry["path_params"]]
    for param in entry["query"]:
        if param["required"] and param["type"] != "object":
            flag = (
                f"param-{param['name']}"
                if param["name"] in {"all", "data", "json", "out"}
                else param["name"]
            )
            argv += [f"--{flag.replace('_', '-')}", _placeholder(param)]
    body = entry.get("body")
    if body and body["media"] == "multipart":
        upload = tmp_path / "upload.bin"
        upload.write_bytes(b"content")
        taken = {p["name"] for p in entry["path_params"] + entry["query"]}
        for field in body["fields"]:
            if not field["required"]:
                continue
            option = (
                f"body-{field['name']}"
                if field["name"] in taken
                else field.get("option", field["name"])
            )
            value = str(upload) if field.get("binary") else _placeholder(field)
            argv += [f"--{option.replace('_', '-')}", value]
    elif body:
        data = {
            f["name"]: (
                {}
                if f["type"] == "object"
                else [_placeholder(f)]
                if f.get("multiple")
                else json.loads(_placeholder(f))
                if f["type"] in ("integer", "number", "boolean")
                else _placeholder(f)
            )
            for f in body["fields"]
            if f["required"]
        }
        argv += ["--data", json.dumps(data)]
    if entry.get("destructive"):
        argv += ["--yes"]
    if entry["response"] == "bytes":
        argv += ["--out", str(tmp_path / "out.bin")]
    return argv


def _response(entry: dict) -> httpx.Response:
    if entry["response"] == "bytes":
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    if entry["response"] == "sse":
        return httpx.Response(
            200,
            content=b'id: 1\ndata: {"type": "ping"}\n\n',
            headers={"content-type": "text/event-stream"},
        )
    if entry["response"] == "none":
        return httpx.Response(204)
    return httpx.Response(200, json={"data": [], "next_cursor": None, "has_more": False})


def _url_pattern(entry: dict) -> str:
    path = re.sub(
        r"{[^}]+}", "[^/]+", re.escape(entry["path"]).replace(r"\{", "{").replace(r"\}", "}")
    )
    return f"^{re.escape(API)}{path}(\\?.*)?$"


@pytest.mark.parametrize("entry", operations(), ids=lambda e: command_name(e))
def test_every_command_sends_its_operation(entry, tmp_path):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route(method=entry["method"], url__regex=_url_pattern(entry)).mock(
            return_value=_response(entry)
        )
        result = runner.invoke(app, _argv(entry, tmp_path))
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    request = route.calls[0].request
    if entry["anonymous"]:
        # security: [] in the contract: never sent with a credential (C059).
        assert "authorization" not in request.headers
    else:
        assert request.headers["authorization"] == "Bearer bk_live_test"
    if entry["method"] != "GET":
        assert request.headers.get("idempotency-key")


@respx.mock
def test_path_arguments_query_options_and_body_fields_map_onto_the_request():
    route = respx.post(f"{API}/v1/projects").respond(201, json={"id": "p1"})
    result = runner.invoke(
        app,
        [
            "api",
            "projects",
            "create-project",
            "--data",
            '{"name": "From data", "description": "kept"}',
            "--name",
            "From option",
            "--tags",
            "a",
            "--tags",
            "b",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content) == {
        "name": "From option",
        "description": "kept",
        "tags": ["a", "b"],
    }
    assert json.loads(result.output) == {"id": "p1"}


@respx.mock
def test_a_path_argument_is_escaped_into_the_path():
    route = respx.get(f"{API}/v1/projects/a%2Fb").respond(200, json={"id": "a/b"})
    result = runner.invoke(app, ["api", "projects", "get-project", "a/b"])
    assert result.exit_code == 0, result.output
    assert route.called


def test_a_missing_required_body_field_is_a_usage_error():
    result = runner.invoke(app, ["api", "projects", "create-project", "--data", "{}"])
    assert result.exit_code == 2
    assert "--name" in result.output


@respx.mock
def test_all_follows_every_page():
    respx.get(f"{API}/v1/projects", params={"cursor": "c2"}).respond(
        200, json={"data": [{"id": "p2"}], "next_cursor": None, "has_more": False}
    )
    respx.get(f"{API}/v1/projects").respond(
        200, json={"data": [{"id": "p1"}], "next_cursor": "c2", "has_more": True}
    )
    result = runner.invoke(app, ["api", "projects", "list-projects", "--all"])
    assert result.exit_code == 0, result.output
    assert [p["id"] for p in json.loads(result.output)] == ["p1", "p2"]


@respx.mock
def test_api_errors_exit_with_the_cli_codes():
    respx.get(f"{API}/v1/projects/missing").respond(
        404, json={"error": {"code": "not_found", "message": "Project not found"}}
    )
    result = runner.invoke(app, ["api", "projects", "get-project", "missing"])
    assert result.exit_code == 4


def test_a_file_response_needs_out_when_printing_to_a_terminal(monkeypatch, tmp_path):
    entry = next(e for e in _builder_operations() if e["response"] == "bytes")
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    with respx.mock(assert_all_called=False) as mock:
        mock.route(method=entry["method"], url__regex=_url_pattern(entry)).mock(
            return_value=_response(entry)
        )
        argv = _argv(entry, tmp_path)[:-2]  # without the trailing --out PATH
        result = _invoke_built(entry, argv)
    assert result.exit_code == 2
    assert "--out" in result.output


def test_a_multipart_command_uploads_the_file(tmp_path):
    entry = next(
        e for e in _builder_operations() if e.get("body") and e["body"]["media"] == "multipart"
    )
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route(method=entry["method"], url__regex=_url_pattern(entry)).mock(
            return_value=_response(entry)
        )
        result = _invoke_built(entry, _argv(entry, tmp_path))
    assert result.exit_code == 0, result.output
    request = route.calls[0].request
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b"content" in request.content


def test_an_event_stream_prints_one_json_event_per_line(tmp_path):
    entry = next(e for e in operations() if e["response"] == "sse")
    with respx.mock(assert_all_called=False) as mock:
        mock.route(method=entry["method"], url__regex=_url_pattern(entry)).mock(
            return_value=_response(entry)
        )
        result = runner.invoke(app, _argv(entry, tmp_path))
    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert events and events[0]["type"] == "ping"


def test_a_query_parameter_named_like_a_command_option_is_prefixed():
    for entry in operations():
        command = build_command(entry)
        flags = [opt for param in command.params for opt in getattr(param, "opts", [])]
        assert len(flags) == len(set(flags)), entry["op"]


@pytest.mark.parametrize("value", [".", ".."])
def test_a_dot_path_value_is_refused_before_anything_is_sent(value):
    # Audit C057: "." and ".." survive percent-encoding and the HTTP client
    # collapses them, so `get-project ..` would have reached GET /v1.
    with respx.mock(assert_all_called=False) as mock:
        anything = mock.route().respond(200, json={})
        result = runner.invoke(app, ["api", "projects", "get-project", value])
    assert result.exit_code == 2
    assert not anything.called


def test_a_destructive_command_needs_yes_when_not_interactive():
    entry = next(e for e in operations() if e["op"] == "DELETE /v1/projects/{project_id}")
    assert entry["destructive"]
    with respx.mock(assert_all_called=False) as mock:
        anything = mock.route().respond(204)
        refused = runner.invoke(app, ["api", "projects", "delete-project", "p1"])
        assert refused.exit_code == 2
        assert not anything.called
        done = runner.invoke(app, ["api", "projects", "delete-project", "p1", "--yes"])
    assert done.exit_code == 0, done.output
    assert anything.call_count == 1


def test_every_destructive_operation_asks_for_confirmation():
    for entry in operations():
        flags = {opt for param in build_command(entry).params for opt in getattr(param, "opts", [])}
        assert ("--yes" in flags) == entry["destructive"], entry["op"]


def test_an_anonymous_operation_works_with_no_credential(monkeypatch):
    monkeypatch.delenv("BEAKR_API_KEY")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{API}/v1/public/openapi.json").respond(200, json={"openapi": "3"})
        result = runner.invoke(app, ["api", "openapi", "public-openapi"])
    assert result.exit_code == 0, result.output
    assert "authorization" not in route.calls[0].request.headers


@respx.mock
def test_a_json_null_answer_prints_null_not_ok():
    # Root cause: a 200 whose JSON body is `null` (no public profile yet) was
    # printed as {"ok": true}, the output reserved for operations that answer
    # nothing; a script could not tell "no profile" from "done".
    entry = next(e for e in _builder_operations() if e["op"] == "GET /v1/me/public-profile")
    respx.get(f"{API}/v1/me/public-profile").respond(200, json=None)
    result = _invoke_built(entry, ["api", entry["area"], entry["action"]])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) is None


@respx.mock
def test_an_uploaded_file_carries_its_own_content_type(tmp_path):
    # Root cause: every file part was sent as application/octet-stream, so the
    # CLI's upload differed from the web app's for the same file (the engine
    # checks a logo's declared image type).
    logo = tmp_path / "brand.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n")
    route = respx.post(f"{API}/v1/canvas-templates/t1/logo").respond(200, json={"ok": True})
    entry = _logo_entry()
    result = _invoke_built(
        entry, ["api", "canvas-templates", entry["action"], "t1", "--file", str(logo)]
    )
    assert result.exit_code == 0, result.output
    body = route.calls[0].request.content
    assert b'filename="brand.png"' in body
    assert b"Content-Type: image/png" in body


def _logo_entry() -> dict:
    entry = next(
        e
        for e in _builder_operations()
        if e["op"] == "POST /v1/canvas-templates/{template_id}/logo"
    )
    file_fields = [f for f in entry["body"]["fields"] if f.get("binary")]
    assert [f["name"] for f in file_fields] == ["file"]
    return entry


def test_a_field_whose_type_is_a_list_of_types_is_read_not_crashed():
    # Root cause: OpenAPI 3.1 may spell `type` as a list (edit_rows `old_rows`
    # items became ["object", "string"]); the manifest used it as a set element
    # and building the `beakr api` table raised TypeError.
    from beakr_cli.api_manifest import _kind

    spec: dict = {"components": {"schemas": {}}}
    rows = {"type": "array", "items": {"type": ["object", "string"]}}
    assert _kind(spec, rows) == {"type": "object"}
    assert _kind(spec, {"type": ["string", "null"]}) == {"type": "string"}
    assert _kind(spec, {"type": "array", "items": {"type": ["integer", "null"]}}) == {
        "type": "integer",
        "multiple": True,
    }
