"""The deployment a self-hosted build talks to: there is no hosted default.

A full deployment's people install this CLI from the release bundle. Falling back to
Beakr's hosted API when no URL was set sent their key (and, after a hosted sign-in,
their files) to Beakr's cloud instead of their installation. Every client the CLI
builds takes its URL from here: ``--api-url`` (or an explicit ``base_url``), then
``BEAKR_BASE_URL`` / ``BEAKR_API_URL``, then the ``api_url`` a sign-in saved. With
none of them it refuses (exit 3) before any request, whatever the installed SDK's
own default is.
"""

from __future__ import annotations

import os

from beakr_sdk.errors import AuthenticationError

BASE_URL_REQUIRED = "base_url_required"
BASE_URL_REQUIRED_MESSAGE = (
    "Set your deployment's URL: --api-url / BEAKR_BASE_URL (for example https://beakr.example.com)."
)
#: How a person signs in to a self-hosted installation.
API_KEY_LOGIN = "beakr auth login --api-url https://<your Beakr host> --with-api-key"


def configured_base_url(explicit: str | None = None) -> str | None:
    """The deployment's API URL, or None when nothing names one."""
    from beakr_sdk.credentials import read_cli_config

    url = (
        explicit
        or os.environ.get("BEAKR_BASE_URL")
        or os.environ.get("BEAKR_API_URL")
        or read_cli_config().get("api_url")
    )
    return str(url).strip().rstrip("/") or None if url else None


def base_url_required() -> AuthenticationError:
    """The refusal when no deployment URL is set."""
    return AuthenticationError(
        BASE_URL_REQUIRED_MESSAGE,
        code=BASE_URL_REQUIRED,
        fix=(
            f"Sign in with an API key from your installation (Settings, Developer): "
            f"`{API_KEY_LOGIN}`, or set BEAKR_BASE_URL."
        ),
    )


def require_base_url(explicit: str | None = None) -> str:
    """The deployment's API URL; :func:`base_url_required` when nothing names one."""
    url = configured_base_url(explicit)
    if not url:
        raise base_url_required()
    return url
