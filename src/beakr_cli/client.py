"""HTTP client for the Beakr API."""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx

from beakr_cli import config


def _resolve_base_url() -> str:
    """Resolve API base URL from env, config, or default."""
    return (
        os.environ.get("BEAKR_API_URL")
        or config.get("api_url")
        or "https://api.thebeakr.com"
    )


def _resolve_api_key() -> str | None:
    """Resolve API key from env or config."""
    return os.environ.get("BEAKR_API_KEY") or config.get("api_key")


def _resolve_org_id() -> str | None:
    """Resolve active org override from env or config."""
    return os.environ.get("BEAKR_ORG_ID") or config.get("org_id")


def _resolve_dev_identity() -> tuple[str, str] | None:
    """Resolve dev identity headers from env or config.

    Returns (identity_id, email) tuple or None.
    Used for local development against a Beakr API running in dev mode.
    """
    identity_id = os.environ.get("BEAKR_DEV_IDENTITY") or config.get("dev_identity_id")
    email = os.environ.get("BEAKR_DEV_EMAIL") or config.get("dev_email")
    if identity_id and email:
        return (identity_id, email)
    return None


def _build_headers() -> dict[str, str]:
    """Build auth headers -- dev headers take priority over bearer token."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    org_id = _resolve_org_id()
    if org_id:
        headers["X-Org-Id"] = org_id

    dev = _resolve_dev_identity()
    if dev:
        identity_id, email = dev
        headers["X-Identity-Id"] = identity_id
        headers["X-Email"] = email
        display_name = os.environ.get("BEAKR_DEV_DISPLAY_NAME") or config.get("dev_display_name")
        if display_name:
            headers["X-Display-Name"] = display_name
        return headers

    api_key = _resolve_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    print(
        "Not authenticated. Run: beakr auth login\n"
        "  Or for local dev: beakr auth dev --identity <id> --email <email>",
        file=sys.stderr,
    )
    raise SystemExit(1)


def get_client() -> httpx.Client:
    """Build an authenticated httpx client."""
    return httpx.Client(
        base_url=_resolve_base_url(),
        headers=_build_headers(),
        timeout=60.0,
    )


def get_async_client() -> httpx.AsyncClient:
    """Build an authenticated async httpx client (for MCP server).

    Timeout is higher than sync client because the research endpoint
    runs an agentic loop that can take 30-45s when downloading and
    reading documents from connected services.
    """
    return httpx.AsyncClient(
        base_url=_resolve_base_url(),
        headers=_build_headers(),
        timeout=90.0,
    )


def scope_params(
    *,
    project: str | None = None,
    personal: bool = False,
) -> dict[str, Any]:
    """Build query params for scope.

    Per-call override takes priority over env var and config.
    No scope = org-wide (RLS ensures user only sees what they have access to).
    If personal=True, scopes to the user's personal project.
    """
    params: dict[str, Any] = {}
    if personal and project:
        raise ValueError("Provide at most one of project or personal scope.")
    if personal:
        project_id = get_personal_project_id()
        if not project_id:
            raise ValueError("Could not resolve your personal project.")
        params["project_id"] = project_id
    else:
        project_id = project or os.environ.get("BEAKR_PROJECT_ID") or config.get("project_id")
        if project_id:
            params["project_id"] = project_id
    return params


# Cache for personal project ID
_personal_project_id: str | None = None


def get_personal_project_id() -> str | None:
    """Fetch and cache the user's personal project ID."""
    global _personal_project_id
    if _personal_project_id is not None:
        return _personal_project_id
    try:
        with get_client() as c:
            resp = c.get("/v1/projects")
            resp.raise_for_status()
            data = resp.json()
            projects = data if isinstance(data, list) else data.get("projects", data)
            for project in projects or []:
                if project.get("project_type") == "personal":
                    _personal_project_id = str(project.get("id", ""))
                    return _personal_project_id
    except Exception:
        return None
    return None


def api_get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    project: str | None = None,
) -> Any:
    """GET request to the Beakr API. Returns parsed JSON."""
    merged = {**scope_params(project=project), **(params or {})}
    with get_client() as c:
        resp = c.get(path, params=merged)
        resp.raise_for_status()
        return resp.json()


def api_post(path: str, json: dict[str, Any] | None = None) -> Any:
    """POST request to the Beakr API. Returns parsed JSON."""
    with get_client() as c:
        resp = c.post(path, json=json)
        resp.raise_for_status()
        return resp.json()


def api_patch(path: str, json: dict[str, Any] | None = None) -> Any:
    """PATCH request to the Beakr API. Returns parsed JSON."""
    with get_client() as c:
        resp = c.patch(path, json=json)
        resp.raise_for_status()
        return resp.json()
