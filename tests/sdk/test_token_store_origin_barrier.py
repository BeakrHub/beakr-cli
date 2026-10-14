"""Logging out of one API stays authoritative when the keychain is unreadable (audit C080).

Root cause: ``clear(origin)`` -- what ``beakr auth logout`` does -- read the stored
logins first and returned early when the origin was not found. With a
temporarily unreadable keychain the read came back empty, so nothing was
cleared, the CLI printed "Logged out", and once the keychain was readable again
the next command authenticated with the same login. Rewriting the logins is not
possible without reading them (other APIs' logins would be lost), so an
unreadable clear now records an origin barrier that hides that origin's older
copies until a confirmed write replaces them. Fake in-memory ``keyring`` only.
"""

from __future__ import annotations

import os
import stat
import sys
from types import SimpleNamespace

import pytest
from beakr_sdk import credentials
from beakr_sdk.credentials import CredentialClearError, StoredTokens, TokenStore
from typer.testing import CliRunner

ORIGIN = "https://api.test"
OTHER = "https://sandbox.test"


@pytest.fixture
def keychain(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "beakr"))
    state = SimpleNamespace(store={}, fail_set=False, fail_delete=False, fail_get=False)

    def set_password(service, username, value):
        if state.fail_set:
            raise RuntimeError("synthetic keychain write failure")
        state.store[service, username] = value

    def delete_password(service, username):
        if state.fail_delete:
            raise RuntimeError("synthetic keychain delete failure")
        state.store.pop((service, username), None)

    def get_password(service, username):
        if state.fail_get:
            raise RuntimeError("synthetic temporary keychain read failure")
        return state.store.get((service, username))

    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            set_password=set_password, get_password=get_password, delete_password=delete_password
        ),
    )
    monkeypatch.setattr(credentials, "_keyring_usable", lambda: True)
    return state


def _pair(tag: str, origin: str) -> StoredTokens:
    return StoredTokens(
        access_token=f"access-{tag}", refresh_token=f"refresh-{tag}", base_url=origin
    )


def _fresh() -> TokenStore:
    return TokenStore(use_keyring=True)


def test_an_unreadable_origin_logout_hides_only_that_origin_after_recovery(keychain):
    store = _fresh()
    store.save(_pair("target", ORIGIN))
    store.save(_pair("other", OTHER))

    keychain.fail_get = True
    assert _fresh().clear(ORIGIN) == "cleared_locally"
    keychain.fail_get = False

    after = _fresh()
    assert after.load(ORIGIN) is None
    assert after.load(OTHER).refresh_token == "refresh-other"


def test_a_new_login_after_the_origin_barrier_is_readable_and_cleans_up(keychain):
    _fresh().save(_pair("old", ORIGIN))
    keychain.fail_get = True
    _fresh().clear(ORIGIN)
    keychain.fail_get = False

    store = _fresh()
    assert store.load(ORIGIN) is None
    store.save(_pair("new", ORIGIN))

    again = _fresh()
    assert again.load(ORIGIN).refresh_token == "refresh-new"
    # The keychain confirmed holding the new copy, so no older copy remains and
    # the barrier file is gone.
    assert not again.barriers_path.exists()


def test_the_barrier_is_removed_once_a_readable_clear_is_confirmed(keychain):
    _fresh().save(_pair("old", ORIGIN))
    keychain.fail_get = True
    _fresh().clear(ORIGIN)
    keychain.fail_get = False
    store = _fresh()
    assert store.barriers_path.exists()

    store.save(_pair("other", OTHER))
    assert _fresh().clear(OTHER) == "cleared"
    assert not _fresh().barriers_path.exists()
    assert _fresh().logins() == {}


def test_the_barrier_file_is_private(keychain):
    _fresh().save(_pair("old", ORIGIN))
    keychain.fail_get = True
    store = _fresh()
    store.clear(ORIGIN)
    mode = stat.S_IMODE(os.stat(store.barriers_path).st_mode)
    assert mode == 0o600


def test_a_readable_clear_of_an_unknown_origin_reports_absent(keychain):
    _fresh().save(_pair("other", OTHER))
    assert _fresh().clear(ORIGIN) == "absent"
    assert not _fresh().barriers_path.exists()


def test_nothing_writable_raises_instead_of_claiming_success(keychain, monkeypatch):
    _fresh().save(_pair("old", ORIGIN))
    keychain.fail_get = True

    def refuse(*_args, **_kwargs):
        raise OSError("read-only home")

    monkeypatch.setattr(credentials, "write_private_file", refuse)
    with pytest.raises(CredentialClearError):
        _fresh().clear(ORIGIN)


def test_cli_logout_reports_a_local_logout_when_the_keychain_cannot_confirm(keychain, monkeypatch):
    from beakr_cli.main import app

    monkeypatch.setenv("BEAKR_API_URL", ORIGIN)
    # The CLI builds its own TokenStore; let it use the fake keychain.
    monkeypatch.delenv("BEAKR_NO_KEYRING", raising=False)
    _fresh().save(_pair("old", ORIGIN))
    monkeypatch.setattr("beakr_cli.commands.auth._revoke_stored_tokens", lambda _origin: False)
    keychain.fail_get = True

    result = CliRunner().invoke(app, ["auth", "logout"])

    assert result.exit_code == 0, result.output
    assert "Logged out in this Beakr profile (BEAKR_HOME)" in result.output
    assert "Other Beakr profiles" in result.output
    keychain.fail_get = False
    assert _fresh().load(ORIGIN) is None


def test_cli_logout_fails_loudly_when_nothing_can_be_recorded(keychain, monkeypatch):
    from beakr_cli.main import app

    monkeypatch.setenv("BEAKR_API_URL", ORIGIN)
    # The CLI builds its own TokenStore; let it use the fake keychain.
    monkeypatch.delenv("BEAKR_NO_KEYRING", raising=False)
    _fresh().save(_pair("old", ORIGIN))
    monkeypatch.setattr("beakr_cli.commands.auth._revoke_stored_tokens", lambda _origin: False)
    keychain.fail_get = True

    def refuse(*_args, **_kwargs):
        raise OSError("read-only home")

    monkeypatch.setattr(credentials, "write_private_file", refuse)
    result = CliRunner().invoke(app, ["auth", "logout"])

    assert result.exit_code == 1
    assert "Not logged out" in result.output
    assert "Logged out." not in result.output
