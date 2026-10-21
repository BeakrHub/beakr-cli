"""Commands for areas a Beakr installation leaves out say so, not "Not Found".

Root cause guarded: a self-hosted installation serves only part of the API
(organization members, external users, end-user tokens, service accounts,
integrations are hosted features) and answers a bare ``{"detail": "Not
Found"}`` for the rest. The CLI printed that 404 as it came, which read like a
broken command. A bare 404 is the router's own answer for a route the server
does not have; a missing record carries an error envelope with its message.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.errors import EXIT_NOT_FOUND
from beakr_cli.main import app, internal_app

API = "https://api.test"
runner = CliRunner()
BARE = {"detail": "Not Found"}

#: A box's own document: projects, no integrations, org admin or embedded mode.
BOX_DOCUMENT = {
    "paths": {
        "/v1/projects": {"get": {}, "post": {}},
        "/v1/me": {"get": {}},
        "/v1/me/api-keys": {"get": {}},
        "/v1/public/openapi.json": {"get": {}},
    }
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.mark.parametrize(
    ("argv", "method", "path", "area"),
    [
        (["integrations", "ls"], "GET", "/v1/integrations", "integrations"),
        (
            ["end-user-tokens", "ls"],
            "GET",
            "/v1/auth/end-user-tokens",
            "end-user tokens",
        ),
        (
            ["members", "ls", "--org", "o1"],
            "GET",
            "/v1/organizations/o1/members",
            "organization members",
        ),
    ],
)
@respx.mock
def test_a_route_the_installation_lacks_reads_as_not_provided(argv, method, path, area):
    respx.route(method=method, url__startswith=f"{API}{path}").respond(404, json=BARE)
    result = runner.invoke(internal_app, argv)
    assert result.exit_code == EXIT_NOT_FOUND, result.output
    assert f"This Beakr installation does not provide {area}." in result.output
    assert "Not Found" not in result.output


@respx.mock
def test_a_missing_record_is_still_reported_as_not_found():
    respx.get(f"{API}/v1/integrations").respond(
        404,
        json={
            "detail": "Connector not found",
            "error": {"code": "not_found", "message": "Connector not found"},
        },
    )
    result = runner.invoke(internal_app, ["integrations", "ls"])
    assert result.exit_code == EXIT_NOT_FOUND
    assert "Connector not found" in result.output
    assert "does not provide" not in result.output


@respx.mock
def test_doctor_lists_the_areas_the_installation_does_not_provide():
    respx.get(f"{API}/v1/public/openapi.json").respond(json=BOX_DOCUMENT)
    respx.get(f"{API}/v1/me").respond(json={"user": {"display_name": "Ada"}})
    respx.get(f"{API}/v1/me/api-keys").respond(
        json=[{"id": "k1", "key_prefix": "bk_live_test", "scopes": None}]
    )

    result = runner.invoke(app, ["doctor", "--json"])

    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    surface = checks["surface"]
    assert surface["status"] == "ok"
    assert surface["documented"] is True
    assert surface["unavailable"] == [
        "organization members",
        "external users",
        "service accounts",
        "organization administration",
        "end-user tokens",
        "integrations",
        # Documented now, and a box leaves it out.
        "skills",
    ]
    assert surface["detail"].startswith("this installation does not provide organization members")


@respx.mock
def test_doctor_on_a_server_without_a_document_assumes_everything():
    respx.get(f"{API}/v1/public/openapi.json").respond(404, json=BARE)
    respx.get(f"{API}/v1/me").respond(json={"user": {"display_name": "Ada"}})
    respx.get(f"{API}/v1/me/api-keys").respond(json=[])

    result = runner.invoke(app, ["doctor", "--json"])

    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["surface"]["documented"] is False
    assert checks["surface"]["unavailable"] == []


@pytest.mark.parametrize(
    ("argv", "area"),
    [
        (["skills", "ls"], "skills"),
        (["members", "ls", "--org", "o1"], "organization members"),
    ],
)
@respx.mock
def test_json_output_for_an_area_the_installation_lacks_is_not_provided(argv, area):
    # Root cause: --json printed the bare 404 as it came ({"code": "not_found",
    # "message": "Not Found"}) while the plain output said "does not provide".
    respx.get(url__startswith=f"{API}/v1/").respond(404, json=BARE)

    result = runner.invoke(internal_app, [*argv, "--json"])

    assert result.exit_code == EXIT_NOT_FOUND, result.output
    assert json.loads(result.stdout) == {
        "error": {
            "message": f"This Beakr installation does not provide {area}.",
            "exit_code": EXIT_NOT_FOUND,
            "code": "not_provided",
        }
    }


@pytest.mark.parametrize(
    ("argv", "area"),
    [
        (["members", "ls"], "organization members"),
        (["members", "rm", "u1", "--yes"], "organization members"),
        (["external-users", "ls"], "external users"),
        (["org", "service-accounts", "ls"], "service accounts"),
        (["org", "credentials", "ls"], "organization administration"),
    ],
)
@respx.mock
def test_the_active_org_lookup_is_named_by_the_commands_area(argv, area):
    # Root cause: with no --org the SDK first reads GET /v1/me/organization,
    # which a box does not have, and the sentence named that raw route.
    respx.get(f"{API}/v1/me/organization").respond(404, json=BARE)

    result = runner.invoke(internal_app, argv)

    assert result.exit_code == EXIT_NOT_FOUND, result.output
    assert f"This Beakr installation does not provide {area}." in result.output
    assert "/v1/" not in result.output


@respx.mock
def test_a_route_no_area_covers_is_named_by_the_command():
    respx.get(url__startswith=f"{API}/v1/workflows").respond(404, json=BARE)

    result = runner.invoke(internal_app, ["workflows", "ls"])

    assert result.exit_code == EXIT_NOT_FOUND, result.output
    assert "This Beakr installation does not provide what `beakr workflows ls` needs." in (
        result.output
    )
    assert "/v1/" not in result.output
