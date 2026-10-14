"""What credential the CLI and SDK would use right now, without network calls."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from beakr_cli import config


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def auth_status_report() -> dict[str, Any]:
    """Which credential the CLI and SDK would use right now, without network calls."""
    from beakr_sdk.credentials import TokenStore, resolve_base_url, resolve_credential
    from beakr_sdk.errors import AuthenticationError

    store = TokenStore()
    report: dict[str, Any] = {
        "base_url": resolve_base_url(),
        "org_override": os.environ.get("BEAKR_ORG_ID") or config.get("org_id"),
        "token_backend": store.backend,
        "authenticated": False,
        "source": None,
        "kind": None,
    }
    try:
        credential = resolve_credential(token_store=store)
    except AuthenticationError:
        return report
    report.update(authenticated=True, source=credential.source, kind=credential.kind)
    if credential.tokens is not None:
        tokens = credential.tokens
        report.update(
            access_expires_at=_iso(tokens.expires_at),
            access_expired=tokens.access_expired(),
            refresh_expires_at=_iso(tokens.refresh_expires_at),
            client_name=tokens.client_name,
            issuer=tokens.issuer,
        )
    elif credential.token:
        report["key_prefix"] = credential.token[:12]
    return report
