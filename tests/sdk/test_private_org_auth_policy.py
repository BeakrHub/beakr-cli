"""The private client's organization sign-in policy resource (``org.policy``)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from .conftest import BASE


# Organization administration is an unlisted route, reached through the
# private client (``beakr_cli._private_api.PrivateBeakr``) the CLI uses.
@pytest.fixture
def client(internal_client):
    return internal_client


ORG = "org-1"


_STORED_POLICY = {
    "allow_personal_keys": False,
    "max_key_lifetime_days": 30,
    "require_step_up_mfa": True,
    "step_up_for_sign_ins": True,
    "allowed_clients": ["beakr-cli"],
    "restrict_oauth_clients": True,
    "approved_oauth_clients": [{"client_id": "c1"}],  # read-only, never sent back
    "require_sso": True,  # retired: the engine refuses it, so it is never sent back
}


def test_policy_set_no_longer_takes_the_retired_sso_fields(client) -> None:
    # The engine refuses require_sso; enterprise SSO is configured elsewhere.
    with pytest.raises(TypeError):
        client.org.policy.set(require_sso=True, org_id=ORG)


@respx.mock
def test_policy_set_changes_only_what_is_passed(client) -> None:
    """Regression: set() sent defaults for every omitted field, so changing one
    field silently opted the org out of step-up MFA."""
    respx.get(f"{BASE}/v1/organizations/{ORG}/auth-policy").respond(json=_STORED_POLICY)
    put = respx.put(f"{BASE}/v1/organizations/{ORG}/auth-policy").mock(
        side_effect=lambda request: httpx.Response(200, json=json.loads(request.content))
    )
    client.org.policy.set(max_key_lifetime_days=90, org_id=ORG)
    sent = json.loads(put.calls.last.request.content)
    never_sent = ("approved_oauth_clients", "require_sso")
    expected = {k: v for k, v in _STORED_POLICY.items() if k not in never_sent}
    assert sent == {**expected, "max_key_lifetime_days": 90}
