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
