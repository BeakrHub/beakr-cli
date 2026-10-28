"""`beakr setup` keeps a browser sign-in instead of asking for an API key.

Regression: setup only looked at ``api_key`` in config.json, so a user who had
signed in with ``beakr auth login`` (device flow, no key) was prompted to paste
a key they never created.
"""

from __future__ import annotations

import time

import respx
from beakr_sdk.credentials import (
    StoredTokens,
    TokenStore,
    resolve_base_url,
    resolve_credential,
)

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


@respx.mock
def test_env_key_is_saved_with_the_api_it_verified_against(monkeypatch) -> None:
    # Root cause: _save_env_key filed $BEAKR_API_KEY under the $BEAKR_BASE_URL
    # origin but did not save that URL as api_url (auth login does), so anything
    # started without the variable (a plugin MCP server, a new shell, an SDK
    # script) resolved production and found no credential.
    _non_interactive(monkeypatch)
    monkeypatch.setenv("BEAKR_BASE_URL", "https://api-sandbox.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_sandbox")
    respx.get("https://api-sandbox.test/v1/me").respond(
        json={"user": {"display_name": "Ada"}, "personal_org": {"name": "Lab"}}
    )

    install._maybe_auth()

    monkeypatch.delenv("BEAKR_BASE_URL")
    monkeypatch.delenv("BEAKR_API_KEY")
    assert resolve_base_url() == "https://api-sandbox.test"
    assert resolve_credential().token == "bk_live_sandbox"


def test_sign_in_only_setup_needs_no_client(monkeypatch) -> None:
    # Root cause: setup refused with "No supported clients detected" before
    # signing in, even when told to wire nothing (--no-skills --no-mcp
    # --no-hooks), so the plugin docs' "install the CLI and sign in" step
    # failed on a machine with only Cursor, VS Code or Gemini CLI.
    _non_interactive(monkeypatch)
    signed_in: list[str] = []
    monkeypatch.setattr(install, "_sign_in_new", lambda: signed_in.append("new"))

    result = install.setup_command(
        client=install.Client.auto,
        scope=install.Scope.user,
        skip_auth=False,
        skip_skills=True,
        skip_mcp=True,
        uninstall=False,
        force=False,
        hooks=False,
    )

    assert signed_in == ["new"]
    assert result["clients"] == []
