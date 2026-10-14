"""``beakr auth token``: the CLI hands out its sign-in without anyone else rotating it.

Root cause guarded (PROSE-449, Codex R067): the TypeScript SDK read only the
credentials *file*, so a default ``beakr auth login`` on macOS -- which keeps
the login in the OS keychain -- was invisible to it, and an expired stored
login failed with ``cli_login_expired`` because Node cannot take the CLI's
refresh ``flock``. The SDK now asks this command instead: it reads the keychain
or the file exactly as the CLI does and refreshes through the CLI's own locked
refresh path. Only an in-memory ``keyring`` fake is used, never the OS
keychain, and every HTTP call is mocked with respx.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from beakr_sdk import client as sync_client
from beakr_sdk import credentials
from beakr_sdk.credentials import StoredTokens, TokenStore
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
#: The OAuth issuer's token endpoint, stored with the sign-in; refresh goes there.
REFRESH = "https://issuer.test/oauth/token"
runner = CliRunner()


@pytest.fixture
def keychain(monkeypatch):
    """A fake ``keyring`` with a real-looking backend, so the store uses the keychain."""
    monkeypatch.delenv("BEAKR_NO_KEYRING", raising=False)
    store: dict = {}
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            set_password=lambda service, user, value: store.__setitem__((service, user), value),
            get_password=lambda service, user: store.get((service, user)),
            delete_password=lambda service, user: store.pop((service, user), None),
        ),
    )
    monkeypatch.setattr(credentials, "_keyring_usable", lambda: True)
    return store


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_BASE_URL", API)


def _login(*, access="bk_at_old", refresh="bk_rt_old", expired=False, base_url=API) -> None:
    TokenStore().save(
        StoredTokens(
            access_token=access,
            refresh_token=refresh,
            expires_at=time.time() + (-10 if expired else 3600),
            refresh_expires_at=time.time() + 86400,
            base_url=base_url,
            org_id="org-1",
            issuer="https://issuer.test",
            token_endpoint=REFRESH,
            oauth_client_id="cli",
        )
    )


def _rotated(access="bk_at_new", refresh="bk_rt_new") -> dict:
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600}


def _token(*args: str):
    return runner.invoke(app, ["auth", "token", "--json", *args])


def _ok(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _refresh_tokens_absent(result) -> None:
    assert "bk_rt_" not in result.output
    assert "refresh_token" not in result.output


def test_prints_a_keychain_login_that_no_file_holds(api, keychain):
    _login(access="bk_at_keychain")
    assert keychain, "the login went to the (fake) keychain"
    assert not credentials.TokenStore().file_path.exists()

    result = _token()
    body = _ok(result)
    assert body["access_token"] == "bk_at_keychain"
    assert body["token_type"] == "Bearer"
    assert body["origin"] == API
    assert body["source"] == "cli_tokens"
    assert body["kind"] == "access_token"
    assert body["expires_at"] > time.time()
    _refresh_tokens_absent(result)


def test_bare_output_is_only_the_token(api):
    _login(access="bk_at_plain")
    result = runner.invoke(app, ["auth", "token"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "bk_at_plain\n"


@respx.mock
def test_an_expired_login_is_refreshed_under_the_cli_lock_and_saved(api, keychain, monkeypatch):
    _login(expired=True)
    held: list[bool] = []
    real_lock = sync_client.refresh_lock

    @contextlib.contextmanager
    def recording_lock():
        with real_lock():
            held.append(True)
            yield
            held.append(False)

    monkeypatch.setattr(sync_client, "refresh_lock", recording_lock)

    def refresh(request: httpx.Request) -> httpx.Response:
        # The refresh is sent only while the cross-process lock is held.
        assert held == [True]
        assert parse_qs(request.content.decode())["refresh_token"] == ["bk_rt_old"]
        return httpx.Response(200, json=_rotated())

    route = respx.post(REFRESH).mock(side_effect=refresh)
    result = _token()
    body = _ok(result)
    assert route.call_count == 1
    assert held == [True, False]
    assert body["access_token"] == "bk_at_new"
    _refresh_tokens_absent(result)
    # The rotated pair is persisted where the CLI keeps it (the keychain here).
    assert TokenStore().load(API).refresh_token == "bk_rt_new"
    assert not TokenStore().file_path.exists()


@respx.mock
def test_a_pair_another_process_rotated_while_waiting_for_the_lock_is_adopted(api, monkeypatch):
    _login(expired=True)
    real_lock = sync_client.refresh_lock

    @contextlib.contextmanager
    def lock_after_another_refresh():
        with real_lock():
            # Another Beakr process refreshed and saved while this one waited.
            _login(access="bk_at_theirs", refresh="bk_rt_theirs")
            yield

    monkeypatch.setattr(sync_client, "refresh_lock", lock_after_another_refresh)
    route = respx.post(REFRESH).respond(json=_rotated())
    body = _ok(_token())
    assert route.call_count == 0, "a spent refresh token must never be presented"
    assert body["access_token"] == "bk_at_theirs"


@respx.mock
def test_a_refused_token_still_stored_is_refreshed(api):
    _login(access="bk_at_refused")
    route = respx.post(REFRESH).respond(json=_rotated())
    digest = hashlib.sha256(b"bk_at_refused").hexdigest()
    body = _ok(_token("--rejected-sha256", digest.upper()))
    assert route.call_count == 1
    assert body["access_token"] == "bk_at_new"


@respx.mock
def test_a_refused_token_the_cli_already_replaced_is_not_refreshed_again(api):
    _login(access="bk_at_current")
    route = respx.post(REFRESH).respond(json=_rotated())
    stale = hashlib.sha256(b"bk_at_previous").hexdigest()
    body = _ok(_token("--rejected-sha256", stale))
    assert route.call_count == 0
    assert body["access_token"] == "bk_at_current"


def test_a_malformed_rejected_digest_is_a_usage_error(api):
    _login()
    result = _token("--rejected-sha256", "not-a-digest")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"


@respx.mock
def test_a_refused_refresh_reports_an_auth_error_without_leaking_tokens(api):
    _login(expired=True)
    respx.post(REFRESH).respond(401, json={"error": {"code": "unauthorized", "message": "revoked"}})
    result = _token()
    assert result.exit_code == 3
    error = json.loads(result.stdout)["error"]
    assert error["code"]
    assert "beakr auth login" in error["message"]
    assert "bk_at_old" not in result.output
    _refresh_tokens_absent(result)


def test_no_login_is_a_clear_auth_error(api):
    result = _token()
    assert result.exit_code == 3
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "unauthorized"
    assert "beakr auth login" in error["message"]


def test_a_logged_out_login_is_never_printed_again(api, keychain):
    _login(access="bk_at_before_logout")
    assert TokenStore().clear(API) == "cleared"
    result = _token()
    assert result.exit_code == 3
    assert "bk_at_before_logout" not in result.output


def test_a_login_for_another_api_is_not_printed(api):
    _login(access="bk_at_prod", base_url="https://api.thebeakr.com")
    result = _token("--api-url", API)
    assert result.exit_code == 3
    assert "bk_at_prod" not in result.output
    # Asked for the API that issued it, it is printed.
    assert _ok(_token("--api-url", "https://api.thebeakr.com/"))["access_token"] == "bk_at_prod"


def test_an_api_key_credential_is_printed_without_an_expiry(api, monkeypatch):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_env")
    body = _ok(_token())
    assert body == {
        "access_token": "bk_live_env",
        "token_type": "Bearer",
        "expires_at": None,
        "origin": API,
        "source": "env",
        "kind": "api_key",
    }


def test_a_dev_identity_has_no_bearer_token(api, monkeypatch):
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "id-1")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "dev@example.com")
    result = _token()
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "no_bearer_token"
