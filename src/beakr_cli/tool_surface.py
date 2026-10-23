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
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ValidationError

#: The listing mode. Every tool is listed by default; ``BEAKR_MCP_TOOLS=eager``
#: lists only the eager set and reveals the rest through ``discover_tools``.
TOOLS_ENV = "BEAKR_MCP_TOOLS"

#: Added to the server instructions in the eager mode: how the rest is reached.
DISCOVERY_NOTE = (
    "\n\nOnly the most-used Beakr tools are listed. Call 'discover_tools' to find the "
    "others (proposals review, project and profile lookups, statistics, conversations, "
    "imports, the audit log); the tools it finds are added to the listing."
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


#: Tools that read the pinned project when given none (``mcp_server._project_by_name_or_id``),
#: and the parameters that name a project for them.
PINNABLE_TOOLS = frozenset({"documents", "research", "wiki_stats", "wiki_graph"})
PROJECT_PARAMETERS = ("project", "project_id")


@cache
def _project_pinned() -> bool:
    """``project_binding.project_is_pinned``, once per server process (its cwd is fixed)."""
    from beakr_cli.project_binding import project_is_pinned

    return project_is_pinned()


def without_project_parameters(tool: MCPTool) -> MCPTool:
    """``tool`` listed without its project parameters.

    In a pinned session an agent offered ``project`` filled it in anyway, and
    mostly with a name it made up, which turned a read of the pinned project into
    a "No single project named ..." refusal. Hidden, the parameter is left
    unset and the tool reads the pinned project. A value passed regardless is
    still honored.
    """
    schema = dict(tool.inputSchema or {})
    properties = {
        key: value
        for key, value in (schema.get("properties") or {}).items()
        if key not in PROJECT_PARAMETERS
    }
    schema["properties"] = properties
    if "required" in schema:
        schema["required"] = [key for key in schema["required"] if key not in PROJECT_PARAMETERS]
    return tool.model_copy(update={"inputSchema": schema})


def lists_every_tool() -> bool:
    """Whether every tool is listed: always, unless ``BEAKR_MCP_TOOLS=eager``.

    Claude Code calls only the tools a server lists, so a tool reachable only
    by name through ``discover_tools`` could not be called from it at all.
    Listing everything costs about 9,000 characters of tool definitions more
    than the eager set; the eager mode stays for clients that want the small
    listing and follow ``tools/list_changed``.
    """
    return os.environ.get(TOOLS_ENV, "").strip().lower() != "eager"


def discovery_note() -> str:
    """The instructions' note on ``discover_tools``, in the eager mode only."""
    return "" if lists_every_tool() else DISCOVERY_NOTE


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
    """A FastMCP server whose listing follows the mode and the installation (W8.2).

    Every tool stays registered, so tools/call reaches any of them by name. The
    listing leaves out:

    - in the eager mode (``BEAKR_MCP_TOOLS=eager``), the discovery tier until
      ``discover_tools`` finds a tool, which :meth:`reveal` adds to the listing;
    - tools this Beakr installation does not provide: those whose documented
      operations its public OpenAPI document leaves out, and any tool whose
      call met the bare 404 of a route the server does not have (:meth:`hide`).

    Either change sends ``notifications/tools/list_changed``; the server
    declares ``tools.listChanged`` so clients re-read the listing.

    ``instructions_for(hidden)`` rebuilds the instructions from the tools the
    installation leaves out, before ``initialize`` is answered, so they never
    send an agent to a tool it cannot see.
    """

    def __init__(
        self,
        *args: Any,
        instructions_for: Callable[[set[str]], str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._instructions_for = instructions_for
        #: Discovery-tier tools ``discover_tools`` found (eager mode).
        self._revealed: set[str] = set()
        #: Tools whose route this installation does not have.
        self._hidden: set[str] = set()

    def initialization_options(self) -> InitializationOptions:
        """The ``initialize`` answer's options, declaring ``tools.listChanged``."""
        return self._mcp_server.create_initialization_options(
            NotificationOptions(tools_changed=True)
        )

    async def run_stdio_async(self) -> None:
        await self.fit_instructions()
        async with stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(read_stream, write_stream, self.initialization_options())

    async def fit_instructions(self) -> None:
        """Rebuild the instructions without the tools this installation does not list."""
        if self._instructions_for is None:
            return
        names = [tool.name for tool in await super().list_tools()]
        hidden = self._hidden | await self._unserved(names)
        self._mcp_server.instructions = self._instructions_for(hidden)

    async def reveal(self, names: list[str]) -> None:
        """Add discovery-tier tools to the eager mode's listing."""
        if lists_every_tool():
            return
        new = set(names) - self._revealed - set(eager_tools())
        if new:
            self._revealed |= new
            await self._listing_changed()

    async def hide(self, name: str) -> None:
        """Stop listing ``name``: this installation does not have its route."""
        if name not in self._hidden:
            self._hidden.add(name)
            await self._listing_changed()

    async def _listing_changed(self) -> None:
        """Tell the client to read the listing again, when a request is in progress."""
        try:
            session = self.get_context().session
        except ValueError:
            return
        try:
            await session.send_tool_list_changed()
        except Exception:
            # The listing is re-read on the next tools/list anyway; a client
            # that went away must not turn a tool result into an error.
            return

    async def _unserved(self, names: list[str]) -> set[str]:
        """Tools whose documented operations this installation's document leaves out."""
        from beakr_cli.capabilities import unserved_tools
        from beakr_cli.sdk import get_async_sdk

        try:
            async with get_async_sdk(timeout=10.0) as sdk:
                capabilities = await sdk.meta.capabilities()
        except Exception:
            # No credential or configuration yet: list everything, as before.
            return set()
        return unserved_tools(capabilities, names)

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
        if _project_pinned():
            tools = [
                without_project_parameters(tool) if tool.name in PINNABLE_TOOLS else tool
                for tool in tools
            ]
        hidden = self._hidden | await self._unserved([tool.name for tool in tools])
        tools = [tool for tool in tools if tool.name not in hidden]
        if lists_every_tool():
            return tools
        listed = set(eager_tools()) | self._revealed
        return [tool for tool in tools if tool.name in listed]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run a tool with forgiving arguments, and return any error compactly.

        Top-level Knowledge options and common aliases are normalized first
        (``mcp_call_shaping``). An argument the tool does not take is then
        refused, as the hosted server's schema check refuses it, rather than
        dropped: a dropped ``scope`` read everything the caller can see. A
        validation failure becomes one line naming the fix; an API refusal, a
        project that names nothing and an error outcome become ``{status,
        error_code, message, fix}`` marked ``isError``.
        """
        from beakr_cli.sdk import MCP_CLIENT_AGENT

        # Writes are attributed to the agent that made them: the MCP client's
        # clientInfo, unless BEAKR_AGENT names one (sdk._agent).
        token = MCP_CLIENT_AGENT.set(self._client_agent())
        try:
            return await self._call_tool(name, arguments)
        finally:
            MCP_CLIENT_AGENT.reset(token)

    def _client_agent(self) -> str | None:
        """``name/version`` of the client that sent ``initialize``, when a request is running."""
        from beakr_cli.sdk import client_agent

        try:
            params = self.get_context().session.client_params
        except ValueError:
            return None
        info = getattr(params, "clientInfo", None)
        if info is None:
            return None
        return client_agent(info.name, info.version)

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        from beakr_cli import mcp_call_shaping
        from beakr_cli.capabilities import NOT_PROVIDED_CODE
        from beakr_cli.mcp_errors import ApiCallError
        from beakr_cli.mcp_tools_platform import ScopeNotFound

        tool = self._tool_manager.get_tool(name)
        schema = tool.parameters if tool is not None else {}
        declared = set((schema.get("properties") or {}).keys())
        given = dict(arguments or {})
        try:
            normalized = mcp_call_shaping.normalize_arguments(name, given, declared)
        except mcp_call_shaping.ConflictingArguments as exc:
            raise ToolError(str(exc)) from exc
        if tool is not None:
            refusal = mcp_call_shaping.unknown_arguments(name, normalized, schema)
            if refusal is not None:
                return _error_result(refusal)
        try:
            # The tool's own dict (``mcp_result.finish`` shape), not FastMCP's
            # conversion of it, which would serialize the whole dict as JSON.
            result = await self._tool_manager.call_tool(
                name, normalized, context=self.get_context(), convert_result=False
            )
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, ValidationError):
                raise ToolError(mcp_call_shaping.validation_message(name, cause, schema)) from cause
            if isinstance(cause, ScopeNotFound):
                return _error_result(
                    mcp_call_shaping.compact_error(name, {**cause.fields, "status": "error"})
                )
            if isinstance(cause, ApiCallError):
                if cause.fields.get("error_code") == NOT_PROVIDED_CODE:
                    await self.hide(name)
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
