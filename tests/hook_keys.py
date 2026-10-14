"""The hook-state key and queue owner for the API-key sign-in the hook tests use."""

from __future__ import annotations

from beakr_sdk import Beakr

from beakr_cli.compounding import current_identity


def identity_for(api_url: str):
    """Resolve under the test's env (``BEAKR_API_URL`` / ``BEAKR_API_KEY``)."""
    with Beakr(base_url=api_url) as sdk:
        identity = current_identity(sdk)
    assert identity is not None
    return identity


def hook_key(api_url: str, session_id: str = "cc-session-1", client: str = "claude-code") -> str:
    return identity_for(api_url).key(client, session_id)
