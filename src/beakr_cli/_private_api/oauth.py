"""The pieces of the Beakr CLI's OAuth sign-in that are not HTTP calls.

The CLI signs in with the OAuth issuer the engine names at ``GET /v1/auth/config``
(Clerk in production): a browser authorization-code flow with PKCE (RFC 7636)
whose redirect lands on a one-shot listener on ``127.0.0.1`` (RFC 8252 section
7.3), or the device grant (RFC 8628) where the issuer offers it. The HTTP calls
(issuer metadata, code exchange, device polling, revocation) are methods of
:class:`beakr_cli._private_api.resources.auth.Auth`, so they share the client's
transport, retries and typed errors; this module holds the parts that are not:
configuration and metadata parsing, PKCE, ``state`` and the loopback listener.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from beakr_sdk.errors import AuthenticationError

#: Asked for alongside the Beakr scopes, so the issuer returns a refresh token.
OFFLINE_ACCESS = "offline_access"

#: The redirect path the loopback listener answers on.
CALLBACK_PATH = "/callback"

#: How long ``beakr auth login`` waits for the browser by default.
DEFAULT_BROWSER_TIMEOUT_SECONDS = 300.0


class OAuthLoginError(AuthenticationError):
    """The sign-in did not complete: denied, tampered with, timed out or unsupported.

    ``code`` is ``access_denied`` (the person declined), ``state_mismatch`` (the
    redirect did not carry this login's ``state``), ``login_timeout``,
    ``device_grant_unsupported``, ``issuer_unavailable`` or the issuer's own
    RFC 6749 error code.
    """


@dataclass(frozen=True)
class IssuerConfig:
    """``GET /v1/auth/config``: who signs the CLI in, as which client, for which scopes."""

    issuer: str
    client_id: str
    scopes: tuple[str, ...]

    @property
    def scope(self) -> str:
        """The ``scope`` parameter: ``offline_access`` and the Beakr scopes."""
        return " ".join([OFFLINE_ACCESS, *(s for s in self.scopes if s != OFFLINE_ACCESS)])

    @classmethod
    def from_response(cls, data: Mapping[str, Any]) -> IssuerConfig:
        # ``issuer`` is the issuer's URL; an engine that names the issuer
        # (``"clerk"``) gives the URL as ``issuer_url``.
        issuer = str(data.get("issuer_url") or data.get("issuer") or "").rstrip("/")
        client_id = str(data.get("client_id") or "")
        raw_scopes = data.get("scopes") or ()
        scopes = tuple(raw_scopes.split() if isinstance(raw_scopes, str) else raw_scopes)
        if not issuer.startswith(("https://", "http://")) or not client_id:
            raise OAuthLoginError(
                "This Beakr API does not offer browser sign-in for the CLI. Sign in with "
                "an API key instead: `beakr auth login --with-api-key` (or set "
                "BEAKR_API_KEY).",
                code="issuer_unavailable",
            )
        return cls(issuer=issuer, client_id=client_id, scopes=tuple(str(s) for s in scopes))


@dataclass(frozen=True)
class IssuerMetadata:
    """The issuer's RFC 8414 metadata, reduced to what the CLI uses."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None = None
    device_authorization_endpoint: str | None = None

    @classmethod
    def from_document(cls, data: Mapping[str, Any], *, issuer: str) -> IssuerMetadata:
        authorization = data.get("authorization_endpoint")
        token = data.get("token_endpoint")
        if not isinstance(authorization, str) or not isinstance(token, str):
            raise OAuthLoginError(
                f"The sign-in issuer {issuer} did not publish its authorization and token "
                "endpoints.",
                code="issuer_unavailable",
            )
        methods = data.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise OAuthLoginError(
                f"The sign-in issuer {issuer} does not support PKCE with S256.",
                code="issuer_unavailable",
            )
        revocation = data.get("revocation_endpoint")
        device = data.get("device_authorization_endpoint")
        return cls(
            issuer=str(data.get("issuer") or issuer).rstrip("/"),
            authorization_endpoint=authorization,
            token_endpoint=token,
            revocation_endpoint=revocation if isinstance(revocation, str) else None,
            device_authorization_endpoint=device if isinstance(device, str) else None,
        )


def metadata_urls(issuer: str) -> list[str]:
    """Where an issuer's metadata may live: RFC 8414 first, then OpenID Connect discovery."""
    base = issuer.rstrip("/")
    return [
        f"{base}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration",
    ]


@dataclass(frozen=True)
class Pkce:
    """An RFC 7636 code verifier and its S256 challenge."""

    verifier: str
    challenge: str

    @classmethod
    def new(cls) -> Pkce:
        # 64 random bytes -> 86 unreserved characters, inside RFC 7636's 43..128.
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return cls(verifier=verifier, challenge=challenge)


def new_state() -> str:
    """An unguessable ``state`` binding the redirect to this sign-in (RFC 6749 10.12)."""
    return secrets.token_urlsafe(32)


def authorization_url(
    metadata: IssuerMetadata,
    config: IssuerConfig,
    *,
    redirect_uri: str,
    state: str,
    pkce: Pkce,
) -> str:
    """The URL the person opens to approve the sign-in."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "scope": config.scope,
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if urlsplit(metadata.authorization_endpoint).query else "?"
    return f"{metadata.authorization_endpoint}{separator}{query}"


_DONE_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Beakr CLI</title></head>"
    "<body style='font-family:system-ui,sans-serif;margin:3rem'>"
    "<h1>{title}</h1><p>{detail}</p></body></html>"
)


class LoopbackRedirect:
    """A one-shot HTTP listener on ``127.0.0.1`` that receives the authorization redirect.

    Binds an ephemeral port (``127.0.0.1:0``) at construction, so
    :attr:`redirect_uri` is known before the browser opens. :meth:`wait` returns
    the redirect's query parameters once a request reaches :data:`CALLBACK_PATH`;
    requests to other paths (a browser's ``/favicon.ico``) are answered 404 and
    ignored. Use it as a context manager so the socket is always closed.
    """

    def __init__(self, host: str = "127.0.0.1") -> None:
        received: dict[str, str] = {}
        arrived = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the http.server hook name
                parts = urlsplit(self.path)
                if parts.path != CALLBACK_PATH or arrived.is_set():
                    self.send_error(404)
                    return
                params = {key: values[0] for key, values in parse_qs(parts.query).items()}
                received.update(params)
                arrived.set()
                failed = "error" in params
                title = "Sign-in was not completed" if failed else "Signed in to Beakr"
                detail = (
                    html.escape(params.get("error_description") or params["error"])
                    if failed
                    else "You can close this tab and return to the terminal."
                )
                body = _DONE_PAGE.format(title=title, detail=detail).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                # The query carries the authorization code: never log requests.
                return

        self._received = received
        self._arrived = arrived
        self._server = HTTPServer((host, 0), Handler)
        self._server.timeout = 0.2
        port = self._server.server_address[1]
        self.redirect_uri = f"http://{host}:{port}{CALLBACK_PATH}"

    def wait(self, timeout: float = DEFAULT_BROWSER_TIMEOUT_SECONDS) -> dict[str, str]:
        """Serve until the redirect arrives; raise ``login_timeout`` after ``timeout`` seconds."""
        deadline = threading.Event()
        timer = threading.Timer(timeout, deadline.set)
        timer.daemon = True
        timer.start()
        try:
            while not self._arrived.is_set():
                if deadline.is_set():
                    raise OAuthLoginError(
                        "Timed out waiting for the browser sign-in. Run `beakr auth login` "
                        "again, or `beakr auth login --device` on a machine without a browser.",
                        code="login_timeout",
                    )
                self._server.handle_request()
        finally:
            timer.cancel()
        return dict(self._received)

    def close(self) -> None:
        self._server.server_close()

    def __enter__(self) -> LoopbackRedirect:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def code_from_redirect(params: Mapping[str, str], *, state: str) -> str:
    """The authorization code from a redirect, after checking ``state`` and errors.

    ``state`` is compared before anything else: a redirect without this login's
    state is not this login's answer, whatever it carries.
    """
    if not secrets.compare_digest(str(params.get("state") or ""), state):
        raise OAuthLoginError(
            "The sign-in redirect did not match this login (state mismatch). Nothing "
            "was saved; run `beakr auth login` again.",
            code="state_mismatch",
        )
    error = params.get("error")
    if error:
        reason = params.get("error_description") or error
        message = (
            "The sign-in was declined in the browser."
            if error == "access_denied"
            else f"The sign-in issuer refused the request: {reason}."
        )
        raise OAuthLoginError(message, code=error)
    code = params.get("code")
    if not code:
        raise OAuthLoginError(
            "The sign-in redirect carried no authorization code.", code="invalid_request"
        )
    return code
