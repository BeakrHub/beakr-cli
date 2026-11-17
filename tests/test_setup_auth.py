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

    monkeypatch.setenv("BEAKR_BASE_URL", "https://beakr.example.com")

    install._maybe_auth()

    assert calls == [("https://beakr.example.com", False)]


def test_setup_with_no_deployment_url_signs_in_nowhere(monkeypatch) -> None:
    import pytest
    from beakr_sdk.errors import AuthenticationError

    _non_interactive(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        "beakr_cli.commands.auth._login_with_oauth",
        lambda base_url, *, open_browser, device: calls.append(base_url),
    )

    with pytest.raises(AuthenticationError):
        install._maybe_auth()
    assert calls == []


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
    # A deployment is named: the self-hosted SDK (0.3.0) refuses the token store
    # without one, as it should; the test is about wiring no client.
    monkeypatch.setenv("BEAKR_BASE_URL", "https://beakr.example.com")
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


def test_the_sdks_no_deployment_url_refusal_is_said_in_the_clis_words(monkeypatch):
    # Root cause (Box 0.1.13 check, 17 Nov): with the self-hosted SDK (0.3.0) and no URL,
    # `beakr setup` reached the SDK's token store first and printed the SDK's wording
    # ("pass base_url=... or set BEAKR_BASE_URL"). The CLI's names --api-url and how
    # to sign in with a key.
    from beakr_sdk.errors import AuthenticationError
    from typer.testing import CliRunner

    from beakr_cli.main import internal_app as app

    def refuse() -> None:
        raise AuthenticationError(
            "Set your deployment's URL: pass base_url=... or set BEAKR_BASE_URL "
            "(for example https://beakr.example.com).",
            code="base_url_required",
        )

    _non_interactive(monkeypatch)
    monkeypatch.setattr(install, "_maybe_auth", refuse)

    result = CliRunner().invoke(app, ["setup", "--no-skills", "--no-mcp", "--no-hooks"])

    text = " ".join(result.output.split())
    assert result.exit_code == 3, result.output
    assert "Set your deployment's URL: --api-url / BEAKR_BASE_URL" in text
    assert "beakr auth login --api-url https://<your Beakr host> --with-api-key" in text
    assert "pass base_url=" not in text


def test_describe_error_says_the_sdks_refusal_as_the_cli_does():
    from beakr_sdk.errors import AuthenticationError

    from beakr_cli.deployment import BASE_URL_REQUIRED_MESSAGE
    from beakr_cli.errors import describe_error

    sdk = AuthenticationError("pass base_url=... or set BEAKR_BASE_URL", code="base_url_required")
    assert BASE_URL_REQUIRED_MESSAGE in describe_error(sdk)
    assert "pass base_url" not in describe_error(sdk)
