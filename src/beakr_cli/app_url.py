"""The Beakr web app's URL, for pages the CLI sends people to.

Some actions are deliberately web-only (creating an API key needs a browser
session; keys and CLI tokens are refused), so the CLI opens the page instead.
Resolution: ``BEAKR_APP_URL``, then a saved ``app_url`` (sign-ins by earlier
versions saved the origin of Beakr's device-flow page), then a mapping from the
API base URL.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from beakr_sdk.credentials import read_cli_config, resolve_base_url

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


def resolve_app_url() -> str:
    explicit = os.environ.get("BEAKR_APP_URL") or read_cli_config().get("app_url")
    if explicit:
        return str(explicit).rstrip("/")
    base = resolve_base_url()
    local = _LOCAL_API.match(base)
    if local:
        port = int(local.group(2))
        return f"{local.group(1)}:{3000 + max(0, port - 8000)}"
    return DEFAULT_APP_URL


def api_keys_page() -> str:
    return resolve_app_url() + API_KEYS_PATH
