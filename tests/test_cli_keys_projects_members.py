"""``beakr keys``, ``beakr projects``, ``beakr init`` and ``beakr members``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app
from beakr_cli.project_binding import read_binding

API = "https://api.test"

PROJECTS = [
    {"id": "p-personal", "name": "Personal", "slug": "personal", "project_type": "personal"},
    {"id": "p-platform", "name": "Platform", "slug": "platform", "project_type": "standard"},
]


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _body(route) -> dict:
    return json.loads(route.calls[0].request.content)


def test_keys_create_opens_the_web_page_and_never_mints_a_key(monkeypatch):
    """Security audit: the API refuses key creation from keys and CLI tokens, so a
    leaked token must not be able to mint a long-lived key through the CLI."""
    opened: list[str] = []
    monkeypatch.setattr("beakr_cli.commands.keys.webbrowser.open", lambda u: opened.append(u))
    monkeypatch.setattr("beakr_cli.commands.keys.is_interactive", lambda: True)

    with respx.mock(assert_all_called=False) as router:
        result = CliRunner().invoke(app, ["keys", "create", "--json"])
        assert not router.calls

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["url"].startswith("https://app.thebeakr.com/settings?tab=api-keys&newKey=")
    assert opened == [body["url"]]
    # Every key records a client, so even a bare request names the CLI.
    assert _decoded_request(body["url"]) == {
        "name": "",
        "scopes": [],
        "project_ids": [],
        "client_name": "beakr-cli",
    }


def test_keys_create_uses_the_app_url_from_sign_in_and_skips_the_browser_when_piped(
    monkeypatch,
):
    from beakr_cli import config

    # Saved for this deployment: the API is in the same parent domain.
    monkeypatch.setenv("BEAKR_API_URL", "https://api.example.test")
    config.set_key("app_url", "https://sandbox-app.example.test")
    opened: list[str] = []
    monkeypatch.setattr("beakr_cli.commands.keys.webbrowser.open", lambda u: opened.append(u))

    result = CliRunner().invoke(app, ["keys", "create", "--json"])

    body = json.loads(result.stdout)
    assert body["url"].startswith("https://sandbox-app.example.test/settings?tab=api-keys&newKey=")
    assert body["opened"] is False
    assert opened == []


def test_app_url_for_a_local_dev_instance(monkeypatch):
    from beakr_cli.app_url import app_url

    monkeypatch.setenv("BEAKR_API_URL", "http://localhost:8003")
    assert app_url().url == "http://localhost:3003"
    assert app_url().guessed is True


# Root cause: keys create sent a self-hosted box's users to http://localhost:3000
# (API on localhost:8000, app on https://localhost:8443). The app URL came only
# from a saved value or a port convention; the API now reports its own app_url
# on GET /v1/auth/config, and that wins over any guess.
@pytest.mark.real_app_url_report
@respx.mock
def test_keys_create_uses_the_app_url_the_api_reports(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", "http://localhost:8000")
    respx.get("http://localhost:8000/v1/auth/config").respond(
        json={
            "client_id": "c",
            "issuer": "https://clerk.test",
            "issuer_url": "https://clerk.test",
            "scopes": [],
            "app_url": "https://localhost:8443/",
        }
    )

    result = CliRunner().invoke(app, ["keys", "create", "--json"])

    body = json.loads(result.stdout)
    assert body["url"].startswith("https://localhost:8443/settings?tab=api-keys&newKey=")
    assert body["app_url_guessed"] is False


@pytest.mark.real_app_url_report
@respx.mock
def test_an_api_that_reports_no_app_url_falls_back_to_a_flagged_guess(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", "http://localhost:8000")
    respx.get("http://localhost:8000/v1/auth/config").respond(
        json={"client_id": "c", "issuer": "https://clerk.test", "scopes": []}
    )
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)

    result = CliRunner().invoke(app, ["keys", "create", "--no-browser"])

    assert "http://localhost:3000/settings?tab=api-keys" in result.stdout
    assert "BEAKR_APP_URL" in result.stderr


# Root cause: an app_url saved by an earlier sign-in was used for every API
# afterwards, so switching to a local box kept links on the first deployment.
def test_a_saved_app_url_for_another_deployment_is_not_used(monkeypatch):
    from beakr_cli import config
    from beakr_cli.app_url import app_url

    config.set_key("app_url", "https://app.thebeakr.com")
    monkeypatch.setenv("BEAKR_API_URL", "http://localhost:8000")
    assert app_url().url == "http://localhost:3000"

    monkeypatch.setenv("BEAKR_API_URL", "https://api.thebeakr.com")
    assert app_url().url == "https://app.thebeakr.com"


@respx.mock
def test_keys_revoke_and_scopes():
    revoke = respx.delete(f"{API}/v1/me/api-keys/k1").respond(204)
    respx.get(f"{API}/v1/me/api-keys/scopes").respond(
        json={"scopes": [{"name": "kb:read", "description": "Read"}]}
    )

    assert CliRunner().invoke(app, ["keys", "revoke", "k1"]).exit_code == 0
    assert revoke.called
    scopes = CliRunner().invoke(app, ["keys", "scopes", "--json"])
    assert json.loads(scopes.stdout) == [{"name": "kb:read", "description": "Read"}]


@respx.mock
def test_projects_create_body():
    route = respx.post(f"{API}/v1/projects").respond(201, json={"id": "p9", "name": "New"})

    result = CliRunner().invoke(
        app, ["projects", "create", "New", "--description", "d", "--tag", "x", "--json"]
    )

    assert result.exit_code == 0, result.output
    assert _body(route) == {"name": "New", "description": "d", "tags": ["x"]}


@respx.mock
def test_projects_archive_resolves_name_and_patches_status():
    respx.get(f"{API}/v1/projects").respond(json=PROJECTS)
    patch = respx.patch(f"{API}/v1/projects/p-platform").respond(
        json={"id": "p-platform", "name": "Platform", "status": "archived"}
    )

    result = CliRunner().invoke(app, ["projects", "archive", "platform", "--json"])

    assert result.exit_code == 0, result.output
    assert _body(patch) == {"status": "archived"}


@respx.mock
def test_projects_show_reads_only_the_documented_project_route():
    # Root cause: `projects show` also read the knowledge profile, a route outside
    # the documented contract (2026-10-12); it now shows the project alone.
    respx.get(f"{API}/v1/projects").respond(json=PROJECTS)
    respx.get(f"{API}/v1/projects/p-platform").respond(json=PROJECTS[1])
    profile = respx.get(f"{API}/v1/knowledge/profiles/project/p-platform")

    result = CliRunner().invoke(app, ["projects", "show", "Platform", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == PROJECTS[1]
    assert not profile.called


@respx.mock
def test_init_binds_the_directory_to_a_named_project():
    respx.get(f"{API}/v1/projects").respond(json=PROJECTS)
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1"})

    result = CliRunner().invoke(app, ["init", "--project", "PLATFORM", "--json"])

    assert result.exit_code == 0, result.output
    binding = read_binding(Path.cwd())
    assert binding == {
        "project_id": "p-platform",
        "name": "Platform",
        "base_url": API,
        "org_id": "org-1",
    }


@respx.mock
def test_init_create_makes_and_binds_a_project():
    respx.post(f"{API}/v1/projects").respond(201, json={"id": "p-new", "name": "Fresh"})
    respx.get(f"{API}/v1/me/organization").respond(404)

    result = CliRunner().invoke(app, ["init", "--create", "Fresh"])

    assert result.exit_code == 0, result.output
    assert read_binding(Path.cwd())["project_id"] == "p-new"


def test_init_without_project_is_a_usage_error_when_not_interactive():
    result = CliRunner().invoke(app, ["init"])

    assert result.exit_code == 2
    assert read_binding(Path.cwd()) is None


@respx.mock
def test_init_unknown_project_exits_not_found():
    respx.get(f"{API}/v1/projects").respond(json=PROJECTS)

    assert CliRunner().invoke(app, ["init", "--project", "nope"]).exit_code == 4


@respx.mock
def test_members_role_sends_role_as_query_parameter():
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1"})
    route = respx.patch(f"{API}/v1/organizations/org-1/members/u1").respond(
        json={"user_id": "u1", "role": "admin"}
    )

    result = CliRunner().invoke(app, ["members", "role", "u1", "admin", "--json"])

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["new_role"] == "admin"


def test_members_role_rejects_unknown_role():
    assert CliRunner().invoke(app, ["members", "role", "u1", "god"]).exit_code == 2


@respx.mock
def test_members_invite_body():
    route = respx.post(f"{API}/v1/invitations").respond(201, json={"id": "inv1"})

    result = CliRunner().invoke(
        app, ["members", "invite", "ada@example.com", "--role", "viewer", "--json"]
    )

    assert result.exit_code == 0, result.output
    assert _body(route) == {"invitee_email": "ada@example.com", "role": "viewer"}


def test_members_rm_refuses_without_yes_when_not_interactive():
    assert CliRunner().invoke(app, ["members", "rm", "u1"]).exit_code == 2


@respx.mock
def test_members_rm_with_explicit_org_and_yes():
    route = respx.delete(f"{API}/v1/organizations/org-9/members/u1").respond(204)

    result = CliRunner().invoke(app, ["members", "rm", "u1", "--org", "org-9", "--yes"])

    assert result.exit_code == 0, result.output
    assert route.called


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [(401, 3), (403, 3), (404, 4), (409, 5), (500, 1)],
)
@respx.mock
def test_api_errors_map_to_stable_exit_codes(status, exit_code):
    respx.post(f"{API}/v1/projects").respond(
        status, json={"detail": "nope", "error": {"code": "x", "message": "nope"}}
    )

    result = CliRunner().invoke(app, ["projects", "create", "Dup"])

    assert result.exit_code == exit_code
    assert "nope" in result.output


# SPEC-184: `beakr keys create --scope ...` exposes scopes, projects and expiry,
# as a request the browser fills in; the CLI still never mints a key itself.


def _decoded_request(url: str) -> dict:
    import base64
    from urllib.parse import parse_qs, urlparse

    raw = parse_qs(urlparse(url).query)["newKey"][0]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


@respx.mock
def test_keys_create_with_scopes_opens_a_prefilled_page_and_mints_nothing(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("beakr_cli.commands.keys.webbrowser.open", lambda u: opened.append(u))
    monkeypatch.setattr("beakr_cli.commands.keys.is_interactive", lambda: True)
    respx.get(f"{API}/v1/me/api-keys/scopes").respond(
        json=[{"name": "kb:read", "description": ""}, {"name": "kb:write", "description": ""}]
    )
    respx.get(f"{API}/v1/projects").respond(
        json={"data": PROJECTS, "next_cursor": None, "has_more": False}
    )
    minted = respx.post(f"{API}/v1/me/api-keys")

    result = CliRunner().invoke(
        app,
        [
            "keys",
            "create",
            "--name",
            "CI",
            "--scope",
            "kb:read",
            "-s",
            "kb:write",
            "--project",
            "Platform",
            "--expires-in",
            "30d",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["url"].startswith("https://app.thebeakr.com/settings?tab=api-keys&newKey=")
    assert opened == [body["url"]]
    assert (
        _decoded_request(body["url"])
        == body["request"]
        == {
            "name": "CI",
            "scopes": ["kb:read", "kb:write"],
            "project_ids": ["p-platform"],
            "client_name": "beakr-cli",
            "expires_in_days": 30,
        }
    )
    assert not minted.called


@respx.mock
def test_keys_create_refuses_an_unknown_scope_before_opening_anything(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("beakr_cli.commands.keys.webbrowser.open", lambda u: opened.append(u))
    respx.get(f"{API}/v1/me/api-keys/scopes").respond(json=[{"name": "kb:read"}])

    result = CliRunner().invoke(app, ["keys", "create", "--scope", "kb:everything"])

    assert result.exit_code == 2
    assert "kb:everything" in result.output
    assert opened == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--expires-in", "0"],
        ["--expires-in", "400"],
        ["--expires-in", "soon"],
        ["--expires-in", "30", "--never-expires"],
    ],
)
def test_keys_create_refuses_a_bad_expiry(argv):
    result = CliRunner().invoke(app, ["keys", "create", *argv])
    assert result.exit_code == 2


def test_keys_create_never_expires_needs_no_lookup_and_prints_the_url_when_piped():
    with respx.mock(assert_all_called=False) as router:
        result = CliRunner().invoke(app, ["keys", "create", "--never-expires", "--json"])
        assert not router.calls
    body = json.loads(result.stdout)
    assert body["opened"] is False
    assert _decoded_request(body["url"]) == {
        "name": "",
        "scopes": [],
        "project_ids": [],
        "client_name": "beakr-cli",
        "never_expires": True,
    }


def test_keys_create_carries_the_named_client_and_refuses_a_blank_one():
    with respx.mock(assert_all_called=False) as router:
        named = CliRunner().invoke(app, ["keys", "create", "--client", " codex ", "--json"])
        blank = CliRunner().invoke(app, ["keys", "create", "--client", "  "])
        assert not router.calls
    assert named.exit_code == 0, named.output
    assert _decoded_request(json.loads(named.stdout)["url"])["client_name"] == "codex"
    assert blank.exit_code == 2
