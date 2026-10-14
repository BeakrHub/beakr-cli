"""The CLI's sign-in, over the unlisted ``GET /v1/auth/config`` and the OAuth issuer.

The Beakr CLI signs in with the OAuth issuer the engine names at
``GET /v1/auth/config`` (Clerk in production), never with Beakr itself:

- in a browser, an authorization-code flow with PKCE whose redirect lands on a
  loopback listener (:meth:`Auth.browser_login`);
- without one, the issuer's device grant, when its metadata offers it
  (:meth:`Auth.start_device_login`, :meth:`Auth.wait_for_device_token`).

Refreshing the stored pair happens in the client (``_base_client``) under the
CLI's refresh lock; :meth:`Auth.revoke_at_issuer` revokes it at sign-out.
Example::

    config = beakr.auth.config()
    metadata = beakr.auth.issuer_metadata(config.issuer)
    tokens = beakr.auth.browser_login(config, metadata, open_url=webbrowser.open)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from beakr_sdk._calls import Call, Flow, Sleep, oauth_form_call
from beakr_sdk._resource import M
from beakr_sdk.errors import BeakrError, NotFoundError, ServerError
from beakr_sdk.resources._base import Resource

from beakr_cli._private_api._generated import operations as ops
from beakr_cli._private_api.oauth import (
    DEFAULT_BROWSER_TIMEOUT_SECONDS,
    IssuerConfig,
    IssuerMetadata,
    LoopbackRedirect,
    OAuthLoginError,
    Pkce,
    authorization_url,
    code_from_redirect,
    metadata_urls,
    new_state,
)

#: RFC 8628: on ``slow_down`` the client adds five seconds to its interval.
_SLOW_DOWN_INCREMENT = 5.0

_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

#: Kept for the device-flow error name callers already catch.
DeviceLoginError = OAuthLoginError


def _oauth_error_code(exc: BeakrError) -> str | None:
    """The RFC 6749 ``error`` string an OAuth endpoint answered with, if any."""
    body = exc.body
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return exc.code


def _issuer_get(url: str) -> Call:
    """A GET to the issuer (not the Beakr API): no Beakr headers, no credential."""
    return Call(
        "GET", url, headers={"Accept": "application/json"}, authenticated=False, external=True
    )


class Auth(Resource[M]):
    """The CLI's OAuth sign-in (``beakr.auth`` on the private client)."""

    def config(self) -> IssuerConfig:
        """``GET /v1/auth/config``: the issuer, the CLI's OAuth client id and its scopes.

        An API that does not serve it (404), or answers ``503
        cli_sign_in_unavailable`` (Clerk sign-in not configured), offers no browser
        sign-in: :class:`OAuthLoginError` ``issuer_unavailable`` says to use an API key.
        """

        def flow() -> Flow:
            try:
                data = yield ops.build_auth_auth_config()
            except NotFoundError:
                data = {}
            except ServerError as exc:
                if exc.code != "cli_sign_in_unavailable":
                    raise
                data = {}
            return IssuerConfig.from_response(data if isinstance(data, dict) else {})

        return self._drive(flow())

    def issuer_metadata(self, issuer: str) -> IssuerMetadata:
        """The issuer's RFC 8414 metadata (falling back to OpenID Connect discovery)."""

        def flow() -> Flow:
            last: BeakrError | None = None
            for url in metadata_urls(issuer):
                try:
                    document = yield _issuer_get(url)
                except BeakrError as exc:
                    last = exc
                    continue
                if isinstance(document, dict):
                    return IssuerMetadata.from_document(document, issuer=issuer)
            raise OAuthLoginError(
                f"Could not read the sign-in issuer's configuration at {issuer}"
                + (f": {last}" if last else "."),
                code="issuer_unavailable",
            )

        return self._drive(flow())

    # -- browser: authorization code + PKCE on a loopback redirect ---------------

    def browser_login(
        self,
        config: IssuerConfig,
        metadata: IssuerMetadata,
        *,
        open_url: Callable[[str], Any],
        timeout: float = DEFAULT_BROWSER_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Sign in through the browser; returns the issuer's token response.

        Binds ``127.0.0.1`` on a free port, calls ``open_url`` with the
        authorization URL (open a browser, or print it), waits for the redirect,
        checks its ``state`` and exchanges the code with the PKCE verifier.
        """
        pkce = Pkce.new()
        state = new_state()
        with LoopbackRedirect() as loopback:
            url = authorization_url(
                metadata, config, redirect_uri=loopback.redirect_uri, state=state, pkce=pkce
            )
            open_url(url)
            params = loopback.wait(timeout)
            redirect_uri = loopback.redirect_uri
        code = code_from_redirect(params, state=state)
        return self.exchange_code(
            config, metadata, code=code, redirect_uri=redirect_uri, verifier=pkce.verifier
        )

    def exchange_code(
        self,
        config: IssuerConfig,
        metadata: IssuerMetadata,
        *,
        code: str,
        redirect_uri: str,
        verifier: str,
    ) -> dict[str, Any]:
        """Exchange an authorization code at the issuer's token endpoint (public client)."""
        return self._run(
            oauth_form_call(
                metadata.token_endpoint,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": config.client_id,
                    "code_verifier": verifier,
                },
            )
        )

    # -- device grant (RFC 8628) -------------------------------------------------

    def start_device_login(self, config: IssuerConfig, metadata: IssuerMetadata) -> dict[str, Any]:
        """Begin a device sign-in; returns ``user_code``, the verification URIs and timing.

        Raises :class:`OAuthLoginError` ``device_grant_unsupported`` when the
        issuer's metadata advertises no ``device_authorization_endpoint``.
        """
        if not metadata.device_authorization_endpoint:
            raise OAuthLoginError(
                "This sign-in issuer does not offer device sign-in. Sign in with an API "
                "key instead: `beakr auth login --with-api-key` (or set BEAKR_API_KEY).",
                code="device_grant_unsupported",
            )
        return self._run_then(
            oauth_form_call(
                metadata.device_authorization_endpoint,
                {"client_id": config.client_id, "scope": config.scope},
            ),
            lambda device: {
                **device,
                "token_endpoint": metadata.token_endpoint,
                "client_id": config.client_id,
            },
        )

    def wait_for_device_token(
        self,
        device: dict[str, Any],
        *,
        on_poll: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> Any:
        """Poll at the issuer's interval until approved; returns the token response.

        Honors ``slow_down`` by lengthening the interval, and raises
        :class:`OAuthLoginError` on ``expired_token``, ``access_denied``, or when
        ``expires_in`` elapses.
        """
        return self._drive(self._wait_flow(device, on_poll=on_poll, clock=clock))

    def _wait_flow(
        self,
        device: dict[str, Any],
        *,
        on_poll: Callable[[], None] | None,
        clock: Callable[[], float],
    ) -> Flow:
        interval = float(device.get("interval") or 5)
        deadline = clock() + float(device.get("expires_in") or 900)
        call = oauth_form_call(
            str(device["token_endpoint"]),
            {
                "grant_type": _DEVICE_GRANT,
                "device_code": str(device["device_code"]),
                "client_id": str(device.get("client_id") or ""),
            },
        )
        while True:
            if clock() >= deadline:
                raise OAuthLoginError(
                    "The sign-in code expired. Run the login again.", code="expired_token"
                )
            yield Sleep(interval)
            if on_poll is not None:
                on_poll()
            try:
                return (yield call)
            except BeakrError as exc:
                code = _oauth_error_code(exc)
                if code == "authorization_pending":
                    continue
                if code == "slow_down":
                    interval += _SLOW_DOWN_INCREMENT
                    continue
                if code in ("expired_token", "access_denied"):
                    message = (
                        "The sign-in code expired. Run the login again."
                        if code == "expired_token"
                        else "The sign-in was declined in the browser."
                    )
                    raise OAuthLoginError(
                        message, code=code, status=exc.status, request_id=exc.request_id
                    ) from exc
                raise

    # -- sign-out -------------------------------------------------------------------

    def revoke_at_issuer(
        self,
        revocation_endpoint: str,
        token: str,
        *,
        client_id: str,
        token_type_hint: str = "refresh_token",
    ) -> None:
        """Revoke a token at the issuer (RFC 7009). Revoking the refresh token ends the login."""
        call = oauth_form_call(
            revocation_endpoint,
            {"token": token, "token_type_hint": token_type_hint, "client_id": client_id},
        )
        self._run(replace(call, response="none"))
