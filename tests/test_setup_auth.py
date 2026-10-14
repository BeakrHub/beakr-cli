"""`beakr setup` keeps a browser sign-in instead of asking for an API key.

Regression: setup only looked at ``api_key`` in config.json, so a user who had
signed in with ``beakr auth login`` (device flow, no key) was prompted to paste
a key they never created.
"""

from __future__ import annotations

import time

import respx
from beakr_sdk.credentials import StoredTokens, TokenStore

from beakr_cli.commands import install


def _non_interactive(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)


@respx.mock
def test_existing_browser_sign_in_is_kept(monkeypatch) -> None:
    _non_interactive(monkeypatch)
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    TokenStore().save(
        StoredTokens(access_token="bk_at_1", refresh_token="bk_rt_1", expires_at=time.time() + 600)
    )
    me = respx.get("https://api.test/v1/me").respond(
        json={"user": {"display_name": "Ada"}, "personal_org": {"name": "Lab"}}
    )
    signed_in: list[str] = []
    monkeypatch.setattr(install, "_sign_in_new", lambda: signed_in.append("new"))

    install._maybe_auth()

    assert me.calls.last.request.headers["authorization"] == "Bearer bk_at_1"
    assert signed_in == []


def test_nothing_configured_signs_in_through_the_browser(monkeypatch) -> None:
    _non_interactive(monkeypatch)
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "beakr_cli.commands.auth._login_with_oauth",
        lambda base_url, *, open_browser, device: calls.append((base_url, open_browser)),
    )

    install._maybe_auth()

    assert calls == [("https://api.thebeakr.com", False)]


@respx.mock
def test_stale_sign_in_is_cleared_and_replaced(monkeypatch) -> None:
    _non_interactive(monkeypatch)
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    TokenStore().save(
        StoredTokens(
            access_token="dead",
            refresh_token="dead",
            token_endpoint="https://issuer.test/oauth/token",
            oauth_client_id="cli",
        )
    )
    respx.get("https://api.test/v1/me").respond(401, json={"detail": "revoked"})
    respx.post("https://issuer.test/oauth/token").respond(400, json={"error": "invalid_grant"})
    signed_in: list[str] = []
    monkeypatch.setattr(install, "_sign_in_new", lambda: signed_in.append("new"))

    install._maybe_auth()

    assert signed_in == ["new"]
    assert TokenStore().load() is None
