"""A local fake of the Beakr API's ``/v1/auth/config`` and an OAuth issuer.

It is a real HTTP server on ``127.0.0.1``, so the SDK's sign-in runs end to end:
metadata discovery, the authorization redirect to the CLI's loopback listener,
the code exchange with PKCE, refresh, revocation and the device grant. The
"browser" is :meth:`FakeIssuer.approve_in_browser`, which follows the
authorization URL the way a browser would after the person consents.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

CLIENT_ID = "cli-client"
SCOPES = ["kb:write", "projects:admin"]


@dataclass
class FakeIssuer:
    """State and behavior switches of the fake issuer."""

    device_grant: bool = True
    #: What the "browser" does at the authorization endpoint.
    consent: str = "approve"  # approve | deny | wrong_state
    #: Device polls answered ``authorization_pending`` before approval.
    pending_polls: int = 1
    device_denied: bool = False
    codes: dict[str, dict[str, str]] = field(default_factory=dict)
    refresh_tokens: set[str] = field(default_factory=set)
    revoked: list[dict[str, str]] = field(default_factory=list)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    authorize_requests: list[dict[str, str]] = field(default_factory=list)
    base: str = ""

    def metadata(self) -> dict[str, Any]:
        document = {
            "issuer": self.base,
            "authorization_endpoint": f"{self.base}/oauth/authorize",
            "token_endpoint": f"{self.base}/oauth/token",
            "revocation_endpoint": f"{self.base}/oauth/token/revoke",
            "code_challenge_methods_supported": ["S256"],
        }
        if self.device_grant:
            document["device_authorization_endpoint"] = f"{self.base}/oauth/device_authorization"
        return document

    def config(self) -> dict[str, Any]:
        return {"issuer": self.base, "client_id": CLIENT_ID, "scopes": SCOPES}

    def issue(self) -> dict[str, Any]:
        refresh = f"rt_{secrets.token_hex(4)}"
        self.refresh_tokens.add(refresh)
        return {
            "access_token": f"oat_{secrets.token_hex(4)}",
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    def approve_in_browser(self, url: str) -> None:
        """Open the authorization URL and follow the redirect to the loopback listener.

        A browser runs beside the CLI, which is still about to start listening,
        so the "browser" works on its own thread.
        """

        def browse() -> None:
            response = httpx.get(url, follow_redirects=False)
            assert response.status_code == 302, response.text
            httpx.get(response.headers["location"], timeout=10)

        threading.Thread(target=browse, daemon=True).start()


def _handler(issuer: FakeIssuer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, body: Any) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode()
            return {key: values[0] for key, values in parse_qs(raw).items()}

        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            if parts.path == "/v1/auth/config":
                self._json(200, issuer.config())
            elif parts.path == "/v1/me":
                self._json(200, {"user": {"display_name": "Ada Lovelace"}})
            elif parts.path == "/.well-known/oauth-authorization-server":
                self._json(200, issuer.metadata())
            elif parts.path == "/oauth/authorize":
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                issuer.authorize_requests.append(query)
                params: dict[str, str] = {"state": query["state"]}
                if issuer.consent == "deny":
                    params["error"] = "access_denied"
                else:
                    code = secrets.token_hex(8)
                    issuer.codes[code] = query
                    params["code"] = code
                    if issuer.consent == "wrong_state":
                        params["state"] = "not-this-login"
                self.send_response(302)
                self.send_header("Location", f"{query['redirect_uri']}?{urlencode(params)}")
                self.end_headers()
            else:
                self._json(404, {"detail": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            form = self._form()
            if parts.path == "/oauth/token":
                issuer.token_requests.append(form)
                self._token(form)
            elif parts.path == "/oauth/token/revoke":
                issuer.revoked.append(form)
                issuer.refresh_tokens.discard(form.get("token", ""))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif parts.path == "/oauth/device_authorization" and issuer.device_grant:
                self._json(
                    200,
                    {
                        "device_code": "dc-1",
                        "user_code": "ABCD-EFGH",
                        "verification_uri": f"{issuer.base}/device",
                        "verification_uri_complete": f"{issuer.base}/device?code=ABCD-EFGH",
                        "interval": 1,
                        "expires_in": 600,
                    },
                )
            else:
                self._json(404, {"detail": "not found"})

        def _token(self, form: dict[str, str]) -> None:
            grant = form.get("grant_type")
            if form.get("client_id") != CLIENT_ID:
                self._json(401, {"error": "invalid_client"})
            elif grant == "authorization_code":
                request = issuer.codes.pop(form.get("code", ""), None)
                digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                if (
                    request is None
                    or request["redirect_uri"] != form.get("redirect_uri")
                    or request["code_challenge"] != challenge
                ):
                    self._json(400, {"error": "invalid_grant"})
                else:
                    self._json(200, issuer.issue())
            elif grant == "refresh_token":
                token = form.get("refresh_token", "")
                if token not in issuer.refresh_tokens:
                    self._json(400, {"error": "invalid_grant"})
                else:
                    issuer.refresh_tokens.discard(token)
                    self._json(200, issuer.issue())
            elif grant == "urn:ietf:params:oauth:grant-type:device_code":
                if issuer.device_denied:
                    self._json(400, {"error": "access_denied"})
                elif issuer.pending_polls > 0:
                    issuer.pending_polls -= 1
                    self._json(400, {"error": "authorization_pending"})
                else:
                    self._json(200, issuer.issue())
            else:
                self._json(400, {"error": "unsupported_grant_type"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


class FakeServer:
    """The fake on a background thread; ``base`` is both the API and the issuer URL."""

    def __init__(self, issuer: FakeIssuer) -> None:
        self.issuer = issuer
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(issuer))
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        issuer.base = self.base
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> FakeServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
