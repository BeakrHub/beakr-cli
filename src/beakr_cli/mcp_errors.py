"""Tool errors that say how to fix the call (developer platform W8.2.3).

The stdio MCP server's tools call the Beakr API over HTTP. A refused call used
to surface as httpx's "Client error '404 Not Found' for url ...", which tells an
agent nothing it can act on. The engine's error envelope carries ``code``,
``message`` and ``fix`` (beakr-engine ``beakr/api/error_fixes.py``); this module
turns a failed response into an exception whose text is that message and fix,
the same text the hosted MCP server returns (``mcp_remote/dispatch.py``).

``ApiCallError`` stays an ``httpx.HTTPStatusError`` so callers that inspect the
response (the conversation import's 409) keep working.

The tools reach the API through the SDK (:func:`call_api`), like every other
part of the CLI; a refusal the SDK raises is re-worded here from the same
envelope.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

import httpx
from beakr_sdk.errors import BeakrError

if TYPE_CHECKING:
    from beakr_cli._private_api import AsyncPrivateBeakr as AsyncBeakr

T = TypeVar("T")


class ApiCallError(httpx.HTTPStatusError):
    """A refused API call, worded for the agent that made it."""

    def __init__(self, message: str, *, request: httpx.Request, response: httpx.Response) -> None:
        super().__init__(message, request=request, response=response)
        #: ``{error_code, message, fix}`` from the envelope, for a structured tool result.
        self.fields = error_fields(response)


def error_fields(response: httpx.Response) -> dict[str, str | None]:
    """``{error_code, message, fix}`` from the error envelope (fix and code may be None)."""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    candidates = [
        error.get("request_id") if isinstance(error, dict) else None,
        response.headers.get("x-request-id"),
    ]
    request_id = next(
        (
            v
            for v in candidates
            if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", v)
        ),
        None,
    )
    correlation = {"request_id": request_id} if request_id else {}
    if isinstance(error, dict) and error.get("message"):
        return {
            **correlation,
            "error_code": str(error["code"]) if error.get("code") else None,
            "message": str(error["message"]),
            "fix": str(error["fix"]) if error.get("fix") else None,
        }
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and detail.get("message"):
        message = str(detail["message"])
    elif isinstance(detail, str) and detail:
        message = detail
    else:
        message = f"HTTP {response.status_code} from {response.request.url.path}"
    return {"error_code": None, "message": message, "fix": None, **correlation}


def describe_error(response: httpx.Response) -> str:
    """``message (code) To fix: fix``, from the error envelope when there is one."""
    fields = error_fields(response)
    code = fields["error_code"]
    text = f"{fields['message']} ({code})" if code else str(fields["message"])
    text = f"{text} To fix: {fields['fix']}" if fields["fix"] else text
    return f"{text} (request_id: {fields['request_id']})" if fields.get("request_id") else text


async def call_api(request: Callable[[AsyncBeakr], Awaitable[T]]) -> T:
    """Run one SDK call for a tool, raising a refusal as :class:`ApiCallError`.

    An error with no HTTP response (the API could not be reached, no
    credential) propagates as the SDK raised it.
    """
    from beakr_cli.sdk import get_async_sdk

    async with get_async_sdk() as sdk:
        try:
            return await request(sdk)
        except BeakrError as exc:
            response = exc.response
            if response is None:
                raise
            raise ApiCallError(
                describe_error(response), request=response.request, response=response
            ) from exc
