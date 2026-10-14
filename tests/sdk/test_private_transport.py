"""Headers, retries, error mapping and pagination of the shared transport."""

from __future__ import annotations

import respx

from .conftest import BASE


@respx.mock
def test_unauthenticated_calls_send_no_credential(internal_client) -> None:
    client = internal_client  # the sign-in configuration is an unlisted route
    route = respx.get(f"{BASE}/v1/auth/config").respond(
        json={"issuer": "https://issuer.test", "client_id": "cli", "scopes": []}
    )
    client.auth.config()
    assert "authorization" not in route.calls.last.request.headers
