"""The CLI's OAuth sign-in against a local fake API and issuer (``tests/oauth_fake.py``).

Browser sign-in is an authorization-code flow with PKCE (S256) whose redirect
lands on a loopback listener; ``state`` binds the redirect to the sign-in. The
device grant is used only when the issuer advertises it. A stored pair refreshes
at the issuer's token endpoint and is revoked at its revocation endpoint.
"""

from __future__ import annotations

import pytest
import respx
from beakr_sdk.credentials import StoredTokens, TokenStore

from beakr_cli._private_api import PrivateBeakr as InternalBeakr
from beakr_cli._private_api.oauth import IssuerConfig, LoopbackRedirect, OAuthLoginError
from tests.oauth_fake import CLIENT_ID, SCOPES, FakeIssuer, FakeServer


@pytest.fixture
def fake():
    with FakeServer(FakeIssuer()) as server:
        yield server


def _client(server: FakeServer) -> InternalBeakr:
    client = InternalBeakr(base_url=server.base, api_key="bk_live_unused")
    client._sleep = lambda _seconds: None
    return client


def test_browser_sign_in_with_pkce_returns_the_issuers_tokens(fake) -> None:
    with _client(fake) as sdk:
        config = sdk.auth.config()
        metadata = sdk.auth.issuer_metadata(config.issuer)
        tokens = sdk.auth.browser_login(
            config, metadata, open_url=fake.issuer.approve_in_browser, timeout=10
        )

    assert tokens["access_token"].startswith("oat_")
    assert tokens["refresh_token"] in fake.issuer.refresh_tokens
    (authorize,) = fake.issuer.authorize_requests
    assert authorize["client_id"] == CLIENT_ID
    assert authorize["code_challenge_method"] == "S256"
    assert authorize["scope"].split() == ["offline_access", *SCOPES]
    assert authorize["redirect_uri"].startswith("http://127.0.0.1:")
    assert authorize["redirect_uri"].endswith("/callback")
    (exchange,) = fake.issuer.token_requests
    # The fake checks the verifier against the challenge; the exchange carries it.
    assert exchange["grant_type"] == "authorization_code"
    assert len(exchange["code_verifier"]) >= 43


def test_a_redirect_with_another_state_is_refused_and_no_code_is_exchanged(fake) -> None:
    fake.issuer.consent = "wrong_state"
    with _client(fake) as sdk:
        config = sdk.auth.config()
        metadata = sdk.auth.issuer_metadata(config.issuer)
        with pytest.raises(OAuthLoginError) as caught:
            sdk.auth.browser_login(
                config, metadata, open_url=fake.issuer.approve_in_browser, timeout=10
            )
    assert caught.value.code == "state_mismatch"
    assert fake.issuer.token_requests == []


def test_a_declined_consent_is_reported_as_access_denied(fake) -> None:
    fake.issuer.consent = "deny"
    with _client(fake) as sdk:
        config = sdk.auth.config()
        metadata = sdk.auth.issuer_metadata(config.issuer)
        with pytest.raises(OAuthLoginError) as caught:
            sdk.auth.browser_login(
                config, metadata, open_url=fake.issuer.approve_in_browser, timeout=10
            )
    assert caught.value.code == "access_denied"
    assert "declined" in str(caught.value)
    assert fake.issuer.token_requests == []


def test_the_loopback_listener_times_out_when_no_browser_returns() -> None:
    with LoopbackRedirect() as loopback, pytest.raises(OAuthLoginError) as caught:
        loopback.wait(timeout=0.3)
    assert caught.value.code == "login_timeout"


def test_a_stored_sign_in_refreshes_at_the_issuer_and_keeps_its_endpoints(
    fake, isolated_home
) -> None:
    with _client(fake) as sdk:
        config = sdk.auth.config()
        metadata = sdk.auth.issuer_metadata(config.issuer)
        response = sdk.auth.browser_login(
            config, metadata, open_url=fake.issuer.approve_in_browser, timeout=10
        )
    stored = StoredTokens.from_token_response(response, base_url=fake.base)
    stored.issuer = metadata.issuer
    stored.token_endpoint = metadata.token_endpoint
    stored.revocation_endpoint = metadata.revocation_endpoint
    stored.oauth_client_id = config.client_id
    stored.expires_at = 0  # expired: the next request refreshes first
    TokenStore(use_keyring=False).save(stored)
    first_refresh = stored.refresh_token

    with InternalBeakr(base_url=fake.base) as sdk:
        sdk._refresh_now()
        refreshed = sdk.credential.tokens

    assert refreshed is not None
    assert refreshed.refresh_token != first_refresh
    assert refreshed.token_endpoint == metadata.token_endpoint
    assert refreshed.revocation_endpoint == metadata.revocation_endpoint
    saved = TokenStore(use_keyring=False).load(fake.base)
    assert saved is not None and saved.refresh_token == refreshed.refresh_token
    assert fake.issuer.token_requests[-1]["grant_type"] == "refresh_token"
    assert fake.issuer.token_requests[-1]["client_id"] == CLIENT_ID


def test_revoking_at_the_issuer_sends_the_refresh_token(fake) -> None:
    with _client(fake) as sdk:
        metadata = sdk.auth.issuer_metadata(fake.base)
        sdk.auth.revoke_at_issuer(metadata.revocation_endpoint or "", "rt_abc", client_id=CLIENT_ID)
    assert fake.issuer.revoked == [
        {"token": "rt_abc", "token_type_hint": "refresh_token", "client_id": CLIENT_ID}
    ]


def test_device_sign_in_polls_until_approved(fake) -> None:
    with _client(fake) as sdk:
        config = sdk.auth.config()
        metadata = sdk.auth.issuer_metadata(config.issuer)
        device = sdk.auth.start_device_login(config, metadata)
        tokens = sdk.auth.wait_for_device_token(device)
    assert device["user_code"] == "ABCD-EFGH"
    assert tokens["access_token"].startswith("oat_")
    polls = [r for r in fake.issuer.token_requests if r["grant_type"].endswith("device_code")]
    assert len(polls) == 2  # one authorization_pending, then the tokens


def test_device_sign_in_is_refused_when_the_issuer_does_not_offer_it(fake) -> None:
    fake.issuer.device_grant = False
    with _client(fake) as sdk:
        config = sdk.auth.config()
        metadata = sdk.auth.issuer_metadata(config.issuer)
        with pytest.raises(OAuthLoginError) as caught:
            sdk.auth.start_device_login(config, metadata)
    assert caught.value.code == "device_grant_unsupported"
    assert "--with-api-key" in str(caught.value)


def test_an_api_without_an_issuer_says_to_use_an_api_key() -> None:
    with pytest.raises(OAuthLoginError) as caught:
        IssuerConfig.from_response({"issuer": "beakr"})
    assert caught.value.code == "issuer_unavailable"
    assert "BEAKR_API_KEY" in str(caught.value)


def test_scopes_may_be_a_space_separated_string() -> None:
    config = IssuerConfig.from_response(
        {"issuer": "https://clerk.test", "client_id": "c", "scopes": "kb:write agent:run"}
    )
    assert config.scope == "offline_access kb:write agent:run"


def test_a_pair_from_beakrs_retired_device_flow_asks_for_a_new_sign_in(isolated_home) -> None:
    TokenStore(use_keyring=False).save(
        StoredTokens(
            access_token="bk_at",
            refresh_token="bk_rt",
            expires_at=0,
            base_url="https://api.test",
        )
    )
    with InternalBeakr(base_url="https://api.test") as sdk, pytest.raises(Exception) as caught:
        sdk._refresh_now()
    assert "beakr auth login" in str(caught.value)


def test_the_issuer_url_may_come_as_issuer_url_beside_the_issuers_name() -> None:
    config = IssuerConfig.from_response(
        {
            "issuer": "clerk",
            "issuer_url": "https://clerk.example.test/",
            "client_id": "c",
            "scopes": ["offline_access", "kb:write"],
        }
    )
    assert config.issuer == "https://clerk.example.test"
    # offline_access is asked for once, whether or not the API lists it.
    assert config.scope == "offline_access kb:write"


@respx.mock
def test_an_api_without_clerk_sign_in_configured_says_to_use_an_api_key() -> None:
    # The engine answers 503 cli_sign_in_unavailable when Clerk sign-in is not set up.
    respx.get("https://api.test/v1/auth/config").respond(
        503, json={"error": {"code": "cli_sign_in_unavailable", "message": "Not configured."}}
    )
    with InternalBeakr(base_url="https://api.test", api_key="bk_live_unused") as sdk:
        sdk._sleep = lambda _seconds: None
        with pytest.raises(OAuthLoginError) as caught:
            sdk.auth.config()
    assert caught.value.code == "issuer_unavailable"
    assert "--with-api-key" in str(caught.value)
