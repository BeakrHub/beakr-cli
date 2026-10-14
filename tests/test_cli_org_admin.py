"""``beakr org``: credentials, service accounts, audit log and auth policy."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
ORG = "org-1"
BASE = f"{API}/v1/organizations/{ORG}"


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _active_org():
    return respx.get(f"{API}/v1/me/organization").respond(json={"id": ORG})


def _refusal(code: str, message: str) -> httpx.Response:
    return httpx.Response(
        403,
        json={
            "detail": {"code": code, "message": message},
            "error": {"code": code, "message": message},
        },
    )


@respx.mock
def test_credentials_ls_resolves_the_org_and_never_shows_secrets():
    active = _active_org()
    respx.get(f"{BASE}/credentials").respond(
        json={
            "data": [
                {
                    "kind": "api_key",
                    "id": "k1",
                    "user_id": "u1",
                    "name": "ci",
                    "scopes": ["kb:read"],
                },
                {"kind": "access_token", "id": "t1", "user_id": "u2", "client_name": "beakr-cli"},
            ]
        }
    )
    result = CliRunner().invoke(app, ["org", "credentials", "ls", "--json"])
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.stdout)] == ["k1", "t1"]
    assert active.called


@respx.mock
def test_org_override_skips_the_active_org_lookup(monkeypatch):
    monkeypatch.setenv("BEAKR_ORG_ID", "org-2")
    route = respx.get(f"{API}/v1/organizations/org-2/credentials").respond(json={"data": []})
    result = CliRunner().invoke(app, ["org", "credentials", "ls", "--json"])
    assert result.exit_code == 0, result.output
    assert route.called


@respx.mock
def test_credentials_revoke_requires_yes_when_not_interactive():
    result = CliRunner().invoke(app, ["org", "credentials", "revoke", "api_key", "k1"])
    assert result.exit_code == 2


@respx.mock
def test_credentials_revoke_deletes_by_kind():
    _active_org()
    route = respx.delete(f"{BASE}/credentials/access_token/t1").respond(204)
    result = CliRunner().invoke(
        app, ["org", "credentials", "revoke", "access_token", "t1", "--yes", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert route.called


@respx.mock
def test_service_account_create_and_key_shows_the_key_once():
    _active_org()
    create = respx.post(f"{BASE}/service-accounts").respond(
        201, json={"user_id": "sa1", "name": "CI bot", "role": "member", "active": True}
    )
    mint = respx.post(f"{BASE}/service-accounts/sa1/keys").respond(
        201,
        json={"id": "k9", "key": "bk_live_secret", "scopes": ["kb:read"], "expires_at": None},
    )
    created = CliRunner().invoke(app, ["org", "service-accounts", "create", "CI bot", "--json"])
    assert created.exit_code == 0, created.output
    assert json.loads(create.calls[0].request.content) == {"name": "CI bot", "role": "member"}

    minted = CliRunner().invoke(
        app,
        ["org", "service-accounts", "key", "sa1", "-s", "kb:read", "-p", "p1", "--client", "ci"],
    )
    assert minted.exit_code == 0, minted.output
    assert "bk_live_secret" in minted.output
    assert json.loads(mint.calls[0].request.content) == {
        "scopes": ["kb:read"],
        "project_ids": ["p1"],
        "client_name": "ci",
    }

    # Without --client the key records the CLI: the API refuses a key with no client.
    CliRunner().invoke(app, ["org", "service-accounts", "key", "sa1", "-s", "kb:read"])
    assert json.loads(mint.calls[1].request.content)["client_name"] == "beakr-cli"


@respx.mock
def test_service_account_disable():
    _active_org()
    route = respx.post(f"{BASE}/service-accounts/sa1/disable").respond(
        json={"user_id": "sa1", "active": False}
    )
    result = CliRunner().invoke(app, ["org", "service-accounts", "disable", "sa1", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


@respx.mock
def test_policy_set_keeps_unspecified_fields():
    _active_org()
    current = {
        "allow_personal_keys": False,
        "max_key_lifetime_days": 30,
        "require_step_up_mfa": True,
        "allowed_clients": ["beakr-cli"],
    }
    respx.get(f"{BASE}/auth-policy").respond(json=current)
    put = respx.put(f"{BASE}/auth-policy").respond(json={**current, "max_key_lifetime_days": None})
    result = CliRunner().invoke(app, ["org", "policy", "set", "--max-key-days", "0", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(put.calls[0].request.content) == {**current, "max_key_lifetime_days": None}


@respx.mock
def test_policy_set_rejects_conflicting_client_options():
    result = CliRunner().invoke(
        app, ["org", "policy", "set", "--allowed-client", "a", "--any-client"]
    )
    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("code", "hint"),
    [
        ("mfa_required", "Verify again in the Beakr web app"),
        ("mfa_enrollment_required", "your account does not have one yet"),
        ("session_required", "Do it in the Beakr web app"),
        ("client_not_allowed", "Organization settings > Developer lists them"),
        ("forbidden", "organization admin"),
    ],
)
@respx.mock
def test_refusals_explain_the_fix(code, hint):
    _active_org()
    respx.post(f"{BASE}/service-accounts").mock(return_value=_refusal(code, "Refused."))
    result = CliRunner().invoke(app, ["org", "service-accounts", "create", "bot"])
    assert result.exit_code == 3
    assert hint in " ".join(result.output.split())


@respx.mock
def test_policy_set_never_opts_out_of_step_up_by_omission():
    """Regression: the CLI fell back to require_step_up_mfa=False when the stored
    policy lacked the field, so changing any other option opted the org out."""
    _active_org()
    respx.get(f"{BASE}/auth-policy").respond(json={"allow_personal_keys": True})
    put = respx.put(f"{BASE}/auth-policy").respond(json={})
    result = CliRunner().invoke(app, ["org", "policy", "set", "--no-personal-keys"])
    assert result.exit_code == 0, result.output
    sent = json.loads(put.calls.last.request.content)
    assert sent == {"allow_personal_keys": False}


def test_audit_rejects_an_unknown_channel_or_credential_kind():
    for args in (["--channel", "email"], ["--credential-kind", "password"]):
        result = CliRunner().invoke(app, ["org", "audit", *args])
        assert result.exit_code == 2, result.output


