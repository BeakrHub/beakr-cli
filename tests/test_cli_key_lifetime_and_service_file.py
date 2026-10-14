"""CLI and SDK for key lifetime policy, legacy keys, browser sessions and service files.

Developer platform W2.2.4 (keys expire unless an admin allows otherwise), W2.2.7
(legacy full-access keys are called out), W3.1 (browser sessions) and W6.1 (the
service-account credentials file a CI job points ``BEAKR_SERVICE_ACCOUNT_FILE`` at).
"""

from __future__ import annotations

import json
import stat

import pytest
import respx
from beakr_sdk.credentials import read_service_account_file
from typer.testing import CliRunner

from beakr_cli._private_api import PrivateBeakr as Beakr
from beakr_cli.main import internal_app as app

API = "https://api.test"
ORG = "org-1"
BASE = f"{API}/v1/organizations/{ORG}"


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_policy_set_allows_non_expiring_keys_without_touching_other_fields(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    current = {"allow_personal_keys": True, "require_step_up_mfa": True}
    respx.get(f"{BASE}/auth-policy").respond(json=current)
    put = respx.put(f"{BASE}/auth-policy").respond(
        json={**current, "allow_non_expiring_keys": True}
    )
    result = CliRunner().invoke(
        app, ["org", "policy", "set", "--allow-non-expiring-keys", "--org", ORG]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(put.calls[0].request.content) == {
        **current,
        "allow_non_expiring_keys": True,
    }
    assert "Non-expiring keys:   allowed" in result.output


@respx.mock
def test_minted_service_key_can_be_saved_as_a_credentials_file(tmp_path):
    respx.post(f"{BASE}/service-accounts/sa1/keys").respond(
        201,
        json={
            "id": "k9",
            "key": "bk_live_secret",
            "name": "deploy",
            "user_id": "sa1",
            "scopes": ["kb:read"],
            "expires_at": "2027-01-01T00:00:00+00:00",
        },
    )
    target = tmp_path / "ci" / "beakr.json"
    result = CliRunner().invoke(
        app,
        [
            "org",
            "service-accounts",
            "key",
            "sa1",
            "-s",
            "kb:read",
            "--credentials-file",
            str(target),
            "--org",
            ORG,
        ],
    )
    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    account = read_service_account_file(target)
    assert (account.api_key, account.base_url, account.org_id) == ("bk_live_secret", API, ORG)


@respx.mock
def test_keys_ls_prompts_to_replace_legacy_keys(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    respx.get(f"{API}/v1/me/api-keys").respond(
        json=[
            {"id": "a", "name": "old", "key_prefix": "bk_live_ab", "scopes": None, "legacy": True},
            {"id": "b", "name": "new", "key_prefix": "bk_live_cd", "scopes": ["kb:read"]},
        ]
    )
    result = CliRunner().invoke(app, ["keys", "ls"])
    assert result.exit_code == 0, result.output
    assert "Replace them with scoped keys" in result.output


@respx.mock
def test_sdk_sends_the_client_a_key_is_for_and_refuses_a_blank_one():
    # Root cause: keys were created with no client at all, so a key's requests
    # could be attributed only by the header each request chose to send.
    create = respx.post(f"{API}/v1/me/api-keys").respond(201, json={"id": "k", "key": "bk"})
    with Beakr(api_key="bk_live_test", base_url=API) as beakr:
        beakr.keys.create("agent", scopes=["kb:read"], client_name=" claude-code ")
        with pytest.raises(ValueError, match="client_name"):
            beakr.keys.create("agent", client_name="   ")
    with Beakr(api_key="bk_live_test", base_url=API, client_name="my-tool/2.0") as beakr:
        beakr.keys.create("tool")
    assert json.loads(create.calls[0].request.content)["client_name"] == "claude-code"
    assert json.loads(create.calls[1].request.content)["client_name"] == "my-tool"
    assert len(create.calls) == 2


@respx.mock
def test_sdk_creates_a_never_expiring_key():
    create = respx.post(f"{API}/v1/me/api-keys").respond(201, json={"id": "k", "key": "bk"})
    with Beakr(api_key="bk_live_test", base_url=API) as beakr:
        beakr.keys.create("forever", scopes=["kb:read"], never_expires=True)
        beakr.keys.create("default")
    # Every key records a client; the SDK names itself when the caller does not.
    assert json.loads(create.calls[0].request.content) == {
        "name": "forever",
        "scopes": ["kb:read"],
        "never_expires": True,
        "client_name": "sdk-python",
    }
    # Leaving never_expires off sends nothing, so the server's 90-day default applies.
    assert json.loads(create.calls[1].request.content) == {
        "name": "default",
        "client_name": "sdk-python",
    }
