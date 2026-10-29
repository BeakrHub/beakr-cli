"""An HTML page where the Beakr API should have answered: name it, never print it.

Pointed at a wrong URL (the web app, a typo, a proxy, a server that is not
Beakr), the CLI used to print the page's raw HTML as the error: the SDK took a
body that is not JSON as the message. Every place that words a refused call for
a person or an agent (the one-line error, ``--json`` errors, MCP tool errors)
asks :func:`wrong_api_page` first, and says where the page came from and which
setting chooses the API instead.
"""

from __future__ import annotations

import html
import re

import httpx

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TITLE_CHARS = 80

#: Where the API URL comes from, in the order the CLI reads them.
API_URL_SETTINGS = (
    "--api-url, BEAKR_BASE_URL (or BEAKR_API_URL), or the URL `beakr auth login` saved; "
    "`beakr auth status` shows the one in use"
)


def is_html_page(response: httpx.Response) -> bool:
    """Whether ``response`` is an HTML page rather than a JSON (or empty) answer."""
    content_type = response.headers.get("content-type", "").lower()
    if "html" in content_type:
        return True
    if "json" in content_type:
        return False
    try:
        return response.text.lstrip()[:1] == "<"
    except httpx.ResponseNotRead:
        return False


def _title(response: httpx.Response) -> str:
    try:
        match = _TITLE.search(response.text)
    except httpx.ResponseNotRead:
        return ""
    if not match:
        return ""
    title = " ".join(html.unescape(match.group(1)).split())
    return title if len(title) <= _TITLE_CHARS else title[: _TITLE_CHARS - 3].rstrip() + "..."


def _address(response: httpx.Response) -> str:
    try:
        url = response.request.url
    except RuntimeError:  # a response built without its request
        return "The server"
    return str(url.copy_with(query=None, fragment=None))


def wrong_api_page(response: httpx.Response | None) -> tuple[str, str] | None:
    """``(message, fix)`` when ``response`` is an HTML page, else ``None``.

    A 5xx page is most often a proxy in front of the right API (a deploy, an
    outage), so its fix says to retry before it says to check the URL.
    """
    if response is None or not is_html_page(response):
        return None
    title = _title(response)
    page = f'an HTML page ("{title}")' if title else "an HTML page"
    message = (
        f"{_address(response)} answered HTTP {response.status_code} with {page}, not the Beakr API."
    )
    if response.status_code >= 500:
        fix = (
            "If that is the right API, a proxy in front of it answered: retry shortly. "
            f"Otherwise set the API URL: {API_URL_SETTINGS}."
        )
    else:
        fix = f"Check the API URL: {API_URL_SETTINGS}."
    return message, fix
