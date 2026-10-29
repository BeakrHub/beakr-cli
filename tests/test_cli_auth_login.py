"""``beakr auth``: browser and device sign-in, API-key login, status and logout.

Sign-in runs against ``tests/oauth_fake.py``, a real local fake of the API's
``/v1/auth/config`` and an OAuth issuer; the "browser" follows the authorization
URL to the CLI's loopback listener. Logout revocation is mocked with respx. The
credential store is the file store (``BEAKR_NO_KEYRING``), never the keychain.
"""

from __future__ import annotations

import json
import stat
from urllib.parse import parse_qs

import pytest
import respx
from beakr_sdk.credentials import StoredTokens, TokenStore, beakr_home
from typer.testing import CliRunner

from beakr_cli import config
from beakr_cli.main import app
from tests.oauth_fake import CLIENT_ID, FakeIssuer, FakeServer

API = "https://api.test"


@pytest.fixture
def opened(monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr("beakr_cli.commands.auth.webbrowser.open", urls.append)
    return urls


@respx.mock
def test_api_key_login_saves_key_in_the_store_in_place_of_the_sign_in():
    TokenStore().save(
        StoredTokens(access_token="bk_at_old", refresh_token="bk_rt_old", base_url=API)
    )
    me = respx.get(f"{API}/v1/me").respond(
        json={"user": {"display_name": "Ada"}, "personal_org": {"name": "Acme"}}
    )

    result = CliRunner().invoke(
        app, ["auth", "login", "--api-key", "bk_live_abc", "--api-url", API]
    )

    assert result.exit_code == 0, result.output
    # Security (B4): the key is in the 0600 token store, never config.json.
    assert "bk_live_abc" not in config.config_file().read_text()
    stored = TokenStore().load(API)
    assert stored is not None and stored.is_api_key and stored.access_token == "bk_live_abc"
    assert me.calls[0].request.headers["Authorization"] == "Bearer bk_live_abc"


def test_help_documents_with_api_key_and_hides_the_old_flag():
    # One documented way to sign in with a key; --api-key still works (tests above).
    result = CliRunner().invoke(app, ["auth", "login", "--help"], env={"COLUMNS": "200"})
    assert "--with-api-key" in result.output
    assert " --api-key " not in result.output


def test_bare_api_key_points_at_with_api_key_instead_of_click_requires_an_argument():
    # Root cause: --api-key is a hidden alias that takes the key as its value,
    # and the docs showed it bare, which Click refused with "Option '--api-key'
    # requires an argument." Adding the key after it puts it in shell history.
    result = CliRunner().invoke(app, ["auth", "login", "--api-key"], env={"COLUMNS": "200"})

    assert result.exit_code == 2
    assert "requires an argument" not in result.output
    assert "use --with-api-key" in result.output
    assert 'echo "$BEAKR_KEY" | beakr auth login --with-api-key' in result.output


@respx.mock
def test_a_key_given_on_the_command_line_warns_about_shell_history():
    respx.get(f"{API}/v1/me").respond(json={"user": {}, "personal_org": {}})

    result = CliRunner().invoke(
        app, ["auth", "login", "--api-key", "bk_live_abc", "--api-url", API]
    )

    assert result.exit_code == 0, result.output
    assert "stays in your shell history" in result.stderr
    assert "--with-api-key" in result.stderr


@respx.mock
def test_with_api_key_reads_the_key_from_stdin_when_piped():
    respx.get(f"{API}/v1/me").respond(json={"user": {}, "personal_org": {}})

    result = CliRunner().invoke(
        app, ["auth", "login", "--with-api-key", "--api-url", API], input="bk_live_piped\n"
    )

    assert result.exit_code == 0, result.output
    assert TokenStore().load(API).access_token == "bk_live_piped"


@respx.mock
def test_failed_api_key_login_removes_the_key():
    respx.get(f"{API}/v1/me").respond(401, json={"detail": "bad key"})

    args = ["auth", "login", "--api-key", "bk_live_bad", "--api-url", API]
    result = CliRunner().invoke(app, args)

    assert result.exit_code == 3
    assert TokenStore().load(API) is None


def test_status_without_credentials_exits_3():
    result = CliRunner().invoke(app, ["auth", "status", "--json"])

    assert result.exit_code == 3
    assert json.loads(result.stdout)["authenticated"] is False


def test_status_reports_env_key_source(monkeypatch):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_env")

    result = CliRunner().invoke(app, ["auth", "status", "--json"])

    report = json.loads(result.stdout)
    assert result.exit_code == 0
    assert report["source"] == "env"
    assert report["kind"] == "api_key"


def test_status_reports_device_tokens_with_expiry():
    TokenStore().save(
        StoredTokens(access_token="bk_at_1", refresh_token="bk_rt_1", expires_at=4_000_000_000)
    )

    result = CliRunner().invoke(app, ["auth", "status", "--json"])

    report = json.loads(result.stdout)
    assert report["source"] == "cli_tokens"
    assert report["kind"] == "access_token"
    assert report["access_expired"] is False
    assert report["token_backend"] == "file"


@respx.mock
def test_top_level_whoami_json(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    respx.get(f"{API}/v1/me").respond(
        json={"user": {"display_name": "Ada"}, "personal_org": {"name": "Acme"}}
    )

    result = CliRunner().invoke(app, ["whoami", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["user"]["display_name"] == "Ada"


# -- browser and device sign-in --------------------------------------------------


@pytest.fixture
def fake():
    with FakeServer(FakeIssuer()) as server:
        yield server


@pytest.fixture
def browser(monkeypatch, fake):
    """The person's browser: opening the sign-in URL approves it (or as configured)."""
    opened: list[str] = []

    def open_url(url: str) -> bool:
        opened.append(url)
        fake.issuer.approve_in_browser(url)
        return True

    monkeypatch.setattr("beakr_cli.commands.auth.webbrowser.open", open_url)
    return opened


def _login(fake, *extra: str):
    return CliRunner().invoke(app, ["auth", "login", "--api-url", fake.base, *extra])


def test_browser_sign_in_saves_the_issuers_tokens_privately(fake, browser):
    result = _login(fake)

    assert result.exit_code == 0, result.output
    assert "Ada Lovelace" in result.output
    (url,) = browser
    assert url.startswith(f"{fake.base}/oauth/authorize?")
    stored = TokenStore().load(fake.base)
    assert stored is not None
    assert stored.access_token.startswith("oat_")
    assert stored.refresh_token in fake.issuer.refresh_tokens
    assert stored.issuer == fake.base
    assert stored.token_endpoint == f"{fake.base}/oauth/token"
    assert stored.revocation_endpoint == f"{fake.base}/oauth/token/revoke"
    assert stored.oauth_client_id == CLIENT_ID
    mode = stat.S_IMODE((beakr_home() / "credentials").stat().st_mode)
    assert mode == 0o600
    assert config.get("api_url") == fake.base


def test_no_browser_prints_the_link_and_opens_nothing(fake, monkeypatch):
    monkeypatch.setattr(
        "beakr_cli.commands.auth.webbrowser.open",
        lambda url: pytest.fail("--no-browser opened a browser"),
    )
    from beakr_cli.commands import auth as auth_command

    printed = auth_command.err_console.print

    def print_and_follow(*args, **kwargs):
        text = str(args[0]) if args else ""
        if text.startswith(f"{fake.base}/oauth/authorize?"):
            # The person copies the printed link into a browser on this machine.
            fake.issuer.approve_in_browser(text)
        return printed(*args, **kwargs)

    monkeypatch.setattr(auth_command.err_console, "print", print_and_follow)

    result = _login(fake, "--no-browser", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["method"] == "browser"
    assert TokenStore().load(fake.base) is not None


def test_a_redirect_for_another_sign_in_saves_nothing(fake, browser):
    fake.issuer.consent = "wrong_state"

    result = _login(fake)

    assert result.exit_code == 3
    assert "state mismatch" in result.output
    assert TokenStore().load(fake.base) is None
    assert fake.issuer.token_requests == []


def test_a_declined_sign_in_exits_3_and_saves_nothing(fake, browser):
    fake.issuer.consent = "deny"

    result = _login(fake)

    assert result.exit_code == 3
    assert "declined" in result.output
    assert TokenStore().load(fake.base) is None


def test_device_sign_in_shows_the_code_and_saves_the_tokens(fake, monkeypatch):
    monkeypatch.setattr("beakr_sdk.client.time.sleep", lambda _s: None)

    result = _login(fake, "--device", "--no-browser")

    assert result.exit_code == 0, result.output
    assert "ABCD-EFGH" in result.output
    stored = TokenStore().load(fake.base)
    assert stored is not None and stored.token_endpoint == f"{fake.base}/oauth/token"


def test_device_sign_in_without_issuer_support_says_to_use_an_api_key(fake):
    fake.issuer.device_grant = False

    result = _login(fake, "--device")

    assert result.exit_code == 3
    assert "does not offer device sign-in" in result.output
    assert "--with-api-key" in result.output
    assert TokenStore().load(fake.base) is None


# -- logout ------------------------------------------------------------------------

ISSUER = "https://issuer.test"
OTHER = "https://sandbox.test"


def _signed_in(base_url: str, refresh: str) -> StoredTokens:
    return StoredTokens(
        access_token=f"oat_{refresh}",
        refresh_token=refresh,
        base_url=base_url,
        issuer=ISSUER,
        token_endpoint=f"{ISSUER}/oauth/token",
        revocation_endpoint=f"{ISSUER}/oauth/token/revoke",
        oauth_client_id=CLIENT_ID,
    )


@respx.mock
def test_logout_revokes_the_refresh_token_at_the_issuer_and_clears_everything():
    config.set_key("api_url", API)
    TokenStore().save(_signed_in(API, "rt_1"))
    config.set_key("project_id", "p1")
    revoke = respx.post(f"{ISSUER}/oauth/token/revoke").respond(200)

    result = CliRunner().invoke(app, ["auth", "logout"])

    assert result.exit_code == 0, result.output
    assert "Tokens revoked" in result.output
    assert [parse_qs(c.request.content.decode()) for c in revoke.calls] == [
        {"token": ["rt_1"], "token_type_hint": ["refresh_token"], "client_id": [CLIENT_ID]}
    ]
    assert TokenStore().load() is None
    assert config.get("api_key") is None
    assert config.get("project_id") is None


@respx.mock
def test_logout_still_clears_when_revocation_fails():
    config.set_key("api_url", API)
    TokenStore().save(_signed_in(API, "rt_1"))
    respx.post(f"{ISSUER}/oauth/token/revoke").respond(503, json={"error": "unavailable"})

    result = CliRunner().invoke(app, ["auth", "logout"])

    assert result.exit_code == 0, result.output
    assert TokenStore().load() is None


def test_logout_of_a_sign_in_from_beakrs_retired_device_flow_just_clears_it():
    config.set_key("api_url", API)
    TokenStore().save(StoredTokens(access_token="bk_at_1", refresh_token="bk_rt_1", base_url=API))

    with respx.mock(assert_all_called=False) as router:
        result = CliRunner().invoke(app, ["auth", "logout", "--json"])
        assert not router.calls

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["tokens_revoked"] is False
    assert TokenStore().load() is None


@respx.mock
def test_logout_leaves_other_apis_logins_alone(monkeypatch):
    # Root cause: logout revoked and deleted every login in the keychain item,
    # which every BEAKR_HOME on the machine shares, so signing out of one API
    # (or one profile) signed the person out of every other one too.
    monkeypatch.setenv("BEAKR_API_URL", API)
    TokenStore().save(_signed_in(API, "rt_here"))
    TokenStore().save(_signed_in(OTHER, "rt_there"))
    revoke = respx.post(f"{ISSUER}/oauth/token/revoke").respond(200)

    result = CliRunner().invoke(app, ["auth", "logout"])

    assert result.exit_code == 0, result.output
    revoked = [parse_qs(c.request.content.decode())["token"] for c in revoke.calls]
    assert revoked == [["rt_here"]]
    assert TokenStore().load(API) is None
    assert TokenStore().load(OTHER).refresh_token == "rt_there"


@respx.mock
def test_logout_all_signs_out_of_every_api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    TokenStore().save(_signed_in(API, "rt_here"))
    TokenStore().save(_signed_in(OTHER, "rt_there"))
    revoke = respx.post(f"{ISSUER}/oauth/token/revoke").respond(200)

    result = CliRunner().invoke(app, ["auth", "logout", "--all"])

    assert result.exit_code == 0, result.output
    assert len(revoke.calls) == 2
    assert TokenStore().logins() == {}
