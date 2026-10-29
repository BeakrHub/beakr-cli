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

from beakr_cli.capabilities import (
    NOT_PROVIDED_CODE,
    area_for,
    is_bare_not_found,
    not_provided_message,
    request_of,
)
from beakr_cli.wrong_api import wrong_api_page

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
    page = wrong_api_page(response)
    if page:
        # Never the page itself: a wrong API URL, or a proxy in front of the API.
        return {**correlation, "error_code": None, "message": page[0], "fix": page[1]}
    if is_bare_not_found(response):
        # The server has no such route: this installation leaves the feature out.
        return {
            **correlation,
            "error_code": NOT_PROVIDED_CODE,
            "message": not_provided_message(area_for(request_of(response)[1])),
            "fix": "Do not retry this tool here; answer with the other Beakr tools.",
        }
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        details = error.get("details")
        field_errors = details.get("errors") if isinstance(details, dict) else None
        return {
            **correlation,
            "error_code": str(error["code"]) if error.get("code") else None,
            # A validation error's envelope message is its first field's text
            # alone; the field names say which argument to change.
            "message": _field_errors(field_errors) or str(error["message"]),
            "fix": str(error["fix"]) if error.get("fix") else None,
        }
    if isinstance(detail, list) and _field_errors(detail):
        message = _field_errors(detail)
    elif isinstance(detail, dict) and detail.get("message"):
        message = str(detail["message"])
    elif isinstance(detail, str) and detail:
        message = detail
    else:
        message = f"HTTP {response.status_code} from {request_of(response)[1] or 'the API'}"
    return {"error_code": None, "message": message, "fix": None, **correlation}


def _field_errors(errors: object) -> str:
    """``body.project_id: Input should be a valid UUID; ...`` from validation errors."""
    if not isinstance(errors, list):
        return ""
    parts = []
    for item in errors:
        if not isinstance(item, dict) or not item.get("msg"):
            continue
        location = ".".join(str(part) for part in item.get("loc") or () if part != "body")
        parts.append(f"{location}: {item['msg']}" if location else str(item["msg"]))
    return "; ".join(parts)


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
