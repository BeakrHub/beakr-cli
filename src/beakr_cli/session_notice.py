"""Lock notices: the API's word that a request locked this session, passed on as it is.

The request whose touch engages a lock (a session goes from open to locked to one
project, or to read-only) answers with ``X-Beakr-Session-Notice`` (percent-encoded
UTF-8) and ``X-Beakr-Session-Lock`` (``locked`` or ``read_only``), for example
"This session is now locked: it can write only to Partner A."

Every client the CLI builds (:mod:`beakr_cli._private_api.client`) hears it on each
response. Inside ``beakr mcp`` a tool call collects what it hears
(:func:`collecting`) and the tool result opens with ``Session: <message>``, as the
engine's remote MCP server words it; at a terminal it is printed to stderr at once.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any
from urllib.parse import unquote

NOTICE_HEADER = "x-beakr-session-notice"
LOCK_HEADER = "x-beakr-session-lock"

#: The notices one MCP tool call heard, in order; None outside a tool call.
_HEARD: ContextVar[list[str] | None] = ContextVar("beakr_session_notices", default=None)


def notice_of(response: Any) -> str | None:
    """The response's lock notice, decoded; None when it carries none."""
    headers = getattr(response, "headers", None)
    raw = headers.get(NOTICE_HEADER) if headers is not None else None
    if not raw:
        return None
    text = unquote(str(raw)).strip()
    return text or None


def hear(response: Any) -> None:
    """Pass on the response's notice: to the running tool call, else to stderr."""
    text = notice_of(response)
    if text is None:
        return
    heard = _HEARD.get()
    if heard is not None:
        if text not in heard:
            heard.append(text)
        return
    from beakr_cli.output import err_console

    err_console.print(f"Session: {text}", markup=False, highlight=False, soft_wrap=True)


async def hear_async(response: Any) -> None:
    """:func:`hear`, as an ``httpx.AsyncClient`` response hook."""
    hear(response)


@contextlib.contextmanager
def collecting() -> Iterator[list[str]]:
    """Collect the notices heard inside the block (one MCP tool call)."""
    heard: list[str] = []
    token = _HEARD.set(heard)
    try:
        yield heard
    finally:
        _HEARD.reset(token)


def prefixed(text: str, notices: list[str]) -> str:
    """``text`` under one ``Session: <message>`` line per notice."""
    if not notices:
        return text
    head = "\n".join(f"Session: {notice}" for notice in notices)
    return f"{head}\n{text}" if text else head


def attach(client: Any, *, is_async: bool) -> None:
    """Hear every response of ``client``'s own HTTP client."""
    http = getattr(client, "_http", None)
    hooks = getattr(http, "event_hooks", None)
    if not isinstance(hooks, dict):
        return
    hook = hear_async if is_async else hear
    responses = list(hooks.get("response") or [])
    if hook not in responses:
        http.event_hooks = {**hooks, "response": [*responses, hook]}
