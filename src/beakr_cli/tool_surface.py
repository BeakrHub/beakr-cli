"""The shared agent tool surface, as vendored from the Beakr engine (W8.2, W8.4).

Beakr Ask, the hosted MCP server and this stdio MCP server describe their tools
with one text, kept in the engine (``beakr/engines/tools/agent_surface.py``) and
committed there as ``docs/api/agent-tool-surface.json``. This package carries a
copy at ``data/agent_tool_surface.json``; ``scripts/sync_tool_surface.py``
refreshes it, and ``tests/test_tool_surface.py`` fails when it differs from the
engine's. The Knowledge tools' text there is Ask's own description, so an agent
connected here reads what Beakr's in-product agent reads.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
from collections.abc import Callable
from functools import cache
from importlib import resources
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ValidationError

#: ``BEAKR_MCP_TOOLS=all`` lists every tool instead of the eager set (W8.2).
TOOLS_ENV = "BEAKR_MCP_TOOLS"

#: Added to the server instructions: how the rest of the surface is reached.
DISCOVERY_NOTE = (
    "\n\nOnly the most-used Beakr tools are listed. Call 'discover_tools' to find the "
    "others (proposals review, project and profile lookups, statistics, conversations, "
    "imports, the audit log); each comes back with its input schema and is called by name."
)


@cache
def document() -> dict[str, Any]:
    text = resources.files("beakr_cli").joinpath("data/agent_tool_surface.json").read_text()
    return json.loads(text)


@cache
def _tools() -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in document()["tools"]}


def description(name: str) -> str:
    """The canonical description of ``name``."""
    return _tools()[name]["description"]


def tier(name: str) -> str:
    """``eager`` or ``discovery``."""
    return _tools()[name]["tier"]


def meta(name: str) -> dict[str, str]:
    """The ``_meta`` a tool carries: its tier, under the engine's key."""
    return {document()["tier_meta_key"]: tier(name)}


def stdio_tools() -> list[str]:
    """Every tool this server should expose, in the surface's order (eager first)."""
    return [tool["name"] for tool in document()["tools"] if "stdio" in tool["surfaces"]]


def tool_kwargs(name: str) -> dict[str, Any]:
    """``FastMCP.tool`` keyword arguments: the shared description and tier."""
    return {"description": description(name), "meta": meta(name)}


def eager_tools() -> list[str]:
    """The tools listed by default, in the surface's order."""
    return list(document()["eager"])


def lists_every_tool() -> bool:
    """Whether this server was asked to list every tool (``BEAKR_MCP_TOOLS=all``)."""
    return os.environ.get(TOOLS_ENV, "").strip().lower() == "all"


def _uniform(fn: Callable, tool: str) -> Callable:
    """``fn`` with its result passed through ``mcp_result.finish``.

    The wrapper keeps ``fn``'s parameters (FastMCP builds the input schema from
    the signature) and declares a dict return, which FastMCP sends as
    structured content.
    """
    from beakr_cli import mcp_result

    signature = inspect.signature(fn, eval_str=True)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return mcp_result.finish(await fn(*args, **kwargs), tool=tool)

    else:

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return mcp_result.finish(fn(*args, **kwargs), tool=tool)

    wrapper.__signature__ = signature.replace(return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    wrapper.__annotations__ = {**getattr(fn, "__annotations__", {}), "return": dict[str, Any]}
    # Marks the registered callable as shaped, for the engine's contract gate.
    wrapper.__beakr_mcp_result__ = tool  # type: ignore[attr-defined]
    return wrapper


class TieredFastMCP(FastMCP):
    """A FastMCP server that lists the eager set and keeps the rest callable (W8.2).

    MCP has no deferred-tool primitive. Every tool stays registered (so tools/call
    reaches any of them by name); only the listing is narrowed, and
    ``discover_tools`` describes the rest. ``BEAKR_MCP_TOOLS=all`` lists everything
    for clients that cannot run a discovery step.
    """

    def tool(self, name: str | None = None, **kwargs: Any) -> Callable[[Callable], Callable]:
        """Register a tool whose every answer is ``{content, coverage, next}`` (SPEC-488).

        The registered callable wraps the tool so ``mcp_result.finish`` bounds and
        shapes whatever it returns, exactly as the hosted server's ``run_tool``
        does; a text cap therefore always marks the result incomplete. The
        decorated function itself is returned unchanged, so code and tests that
        call it directly see its own return value.

        No ``outputSchema`` is declared: :meth:`call_tool` sends each result
        once, as text (:mod:`beakr_cli.mcp_render`), and a declared schema would
        oblige every result to repeat itself as ``structuredContent``.
        """
        kwargs["structured_output"] = False
        register = super().tool(name=name, **kwargs)

        def decorator(fn: Callable) -> Callable:
            register(_uniform(fn, name or fn.__name__))
            return fn

        return decorator

    async def list_tools(self) -> list[MCPTool]:
        order = {name: index for index, name in enumerate(stdio_tools())}
        tools = sorted(await super().list_tools(), key=lambda t: order.get(t.name, len(order)))
        if lists_every_tool():
            return tools
        eager = set(eager_tools())
        return [tool for tool in tools if tool.name in eager]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run a tool with forgiving arguments, and return any error compactly.

        Top-level Knowledge options and common aliases are normalized first
        (``mcp_call_shaping``). A validation failure becomes one line naming the
        fix; an API refusal or an error outcome becomes ``{status, error_code,
        message, fix}`` marked ``isError``.
        """
        from beakr_cli import mcp_call_shaping
        from beakr_cli.mcp_errors import ApiCallError

        tool = self._tool_manager.get_tool(name)
        schema = tool.parameters if tool is not None else {}
        declared = set((schema.get("properties") or {}).keys())
        given = dict(arguments or {})
        normalized = mcp_call_shaping.normalize_arguments(name, given, declared)
        try:
            # The tool's own dict (``mcp_result.finish`` shape), not FastMCP's
            # conversion of it, which would serialize the whole dict as JSON.
            result = await self._tool_manager.call_tool(
                name, normalized, context=self.get_context(), convert_result=False
            )
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, ValidationError):
                raise ToolError(
                    mcp_call_shaping.validation_message(name, cause, schema, given)
                ) from cause
            if isinstance(cause, ApiCallError):
                return _error_result(
                    mcp_call_shaping.compact_error(name, {**cause.fields, "status": "error"})
                )
            raise
        body = result if isinstance(result, dict) else {"content": result}
        if body.get("status") == "error":
            return _error_result(mcp_call_shaping.compact_error(name, body))
        from beakr_cli.mcp_render import render

        return CallToolResult(content=[TextContent(type="text", text=render(name, body))])


def _error_result(body: dict[str, Any]) -> CallToolResult:
    """An error, once, as compact JSON text: ``{status, error_code, message, fix}``."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(body, separators=(", ", ": ")))],
        isError=True,
    )
