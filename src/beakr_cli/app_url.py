"""The Beakr web app's URL, for pages the CLI sends people to.

Some actions are deliberately web-only (creating an API key needs a browser
session; keys and CLI tokens are refused), so the CLI opens the page instead.
Resolution (:func:`app_url`): ``BEAKR_APP_URL``, then the API's own report
(``app_url`` on ``GET /v1/auth/config``), then a saved ``app_url`` for the same
deployment (sign-ins by earlier versions saved the origin of Beakr's
device-flow page), then a guess from the API base URL.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from beakr_sdk.credentials import DEFAULT_BASE_URL, read_cli_config, resolve_base_url

DEFAULT_APP_URL = "https://app.thebeakr.com"

#: Where the API keys tab lives in beakr-web (app/(signed-in)/settings).
API_KEYS_PATH = "/settings?tab=api-keys"

#: Local dev: the web dev server is API port 8000+N -> web port 3000+N.
_LOCAL_API = re.compile(r"^(https?://(?:localhost|127\.0\.0\.1)):(\d+)$")


def origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class AppUrl:
    """The web app's URL and where it came from.

    ``guessed`` is true when nothing said it (no ``BEAKR_APP_URL``, the API did
    not report one, no saved one for this API): the URL is derived from the API
    URL by convention and may not be where this deployment serves its app.
    """

    url: str
    guessed: bool


def resolve_app_url() -> str:
    return app_url().url


def app_url() -> AppUrl:
    """``BEAKR_APP_URL``, then the API's own report, then a saved URL for this API, then a guess.

    The guess maps a local API port to the web dev server (8000+N -> 3000+N)
    and anything else to Beakr's hosted app; a self-hosted box (API on
    localhost:8000, app on https://localhost:8443) is why the API's report
    comes first.
    """
    explicit = os.environ.get("BEAKR_APP_URL")
    if explicit:
        return AppUrl(explicit.rstrip("/"), guessed=False)
    base = resolve_base_url()
    reported = _reported_app_url(base)
    if reported:
        return AppUrl(reported, guessed=False)
    saved = read_cli_config().get("app_url")
    if saved and _same_deployment(str(saved), base):
        return AppUrl(str(saved).rstrip("/"), guessed=False)
    local = _LOCAL_API.match(base)
    if local:
        port = int(local.group(2))
        return AppUrl(f"{local.group(1)}:{3000 + max(0, port - 8000)}", guessed=True)
    return AppUrl(DEFAULT_APP_URL, guessed=base != DEFAULT_BASE_URL)


_reported: dict[str, str | None] = {}


def _reported_app_url(base_url: str) -> str | None:
    """``app_url`` from the API's ``/v1/auth/config``, asked once per process."""
    if base_url not in _reported:
        from beakr_cli._private_api import PrivateBeakr
        from beakr_cli.sdk import CLI_CLIENT_NAME

        try:
            with PrivateBeakr(base_url=base_url, client_name=CLI_CLIENT_NAME) as sdk:
                _reported[base_url] = sdk.auth.app_url()
        except Exception:
            # Unreachable or refusing: fall back rather than fail the command.
            _reported[base_url] = None
    return _reported[base_url]


def _same_deployment(app: str, api: str) -> bool:
    """Whether a saved app URL belongs to the configured API.

    Sign-ins by earlier versions saved the app URL once; switching to another
    API (a local box, sandbox) kept sending people to the first one's app. A
    saved URL is used only when its host is the API's host or shares its
    parent domain (``app.thebeakr.com`` for ``api.thebeakr.com``).
    """
    app_host = (urlsplit(app).hostname or "").lower()
    api_host = (urlsplit(api).hostname or "").lower()
    if not app_host or not api_host:
        return False
    return app_host == api_host or _parent(app_host) == _parent(api_host)


def _parent(host: str) -> str:
    labels = host.split(".")
    return ".".join(labels[1:]) if len(labels) > 2 else host
