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
import math
import os
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import anyio
import anyio.abc
from mcp.server.lowlevel import NotificationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from mcp.types import Tool as MCPTool
from mcp.types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import ValidationError

#: The listing mode. Every tool is listed by default; ``BEAKR_MCP_TOOLS=eager``
#: lists only the eager set and reveals the rest through ``discover_tools``.
TOOLS_ENV = "BEAKR_MCP_TOOLS"

#: Added to the server instructions in the eager mode: how the rest is reached.
DISCOVERY_NOTE = (
    "\n\nOnly the most-used Beakr tools are listed. Call 'discover_tools' to find the "
    "others (proposals review, project and profile lookups, statistics, conversations, "
    "imports, the audit log, project setup); the tools it finds are added to the listing."
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


#: This server's name in the surface's per-surface annotations.
SURFACE = "stdio"


def title(name: str) -> str:
    """The human name an MCP client shows for ``name``."""
    return _tools()[name]["title"]


def annotations(name: str) -> ToolAnnotations:
    """``name``'s MCP annotations on this server: its title and four behavior hints.

    Codex treats a tool not marked read-only as possibly side-effecting: it asks
    before every call in a read-only sandbox and refuses the call under
    ``approval_policy="never"`` (any ``codex exec``). The hints are the engine's,
    decided per tool from what its handler does; a tool that behaves differently
    here (``update_beakr`` upgrades this installation, ``beakr_version`` checks
    PyPI) carries a ``surface_annotations`` entry for this server.
    """
    tool = _tools()[name]
    hints = (tool.get("surface_annotations") or {}).get(SURFACE) or tool["annotations"]
    return ToolAnnotations(title=tool["title"], **hints)


def tool_kwargs(name: str) -> dict[str, Any]:
    """``MCPServer.tool`` keyword arguments: the shared description, tier, title and hints."""
    return {
        "description": description(name),
        "meta": meta(name),
        "title": title(name),
        "annotations": annotations(name),
    }


def eager_tools() -> list[str]:
    """The tools listed by default, in the surface's order."""
    return list(document()["eager"])


@cache
def confirm_lock_tools() -> frozenset[str]:
    """The tools that take ``confirm_lock``: their calls can touch a project (boundaries)."""
    return frozenset(document().get("confirm_lock") or ())


def listed_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """A tool's input schema as listed: with ``confirm_lock`` where its calls can touch a project.

    The shared surface's one property (``confirm_lock_property``), as the hosted
    server lists it (``server._listed_schema``); :meth:`TieredMCPServer.call_tool`
    takes it off the call before the tool's own schema checks it.
    """
    from beakr_cli.lock_confirm import ARGUMENT

    prop = document().get("confirm_lock_property")
    if name not in confirm_lock_tools() or not isinstance(prop, dict):
        return schema
    return {**schema, "properties": {**(schema.get("properties") or {}), ARGUMENT: dict(prop)}}


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
    schema = dict(tool.input_schema or {})
    properties = {
        key: value
        for key, value in (schema.get("properties") or {}).items()
        if key not in PROJECT_PARAMETERS
    }
    schema["properties"] = properties
    if "required" in schema:
        schema["required"] = [key for key in schema["required"] if key not in PROJECT_PARAMETERS]
    return tool.model_copy(update={"input_schema": schema})


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

    The wrapper keeps ``fn``'s parameters (MCPServer builds the input schema from
    the signature) and declares a dict return; :meth:`TieredMCPServer.call_tool`
    renders that dict as the one text block the client receives.
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


#: The request context of the tool call in progress (:meth:`TieredMCPServer.get_context`).
_CALL_CONTEXT: ContextVar[Context | None] = ContextVar("beakr_mcp_call_context", default=None)

#: Seconds the first tools/list of a run waits for the installation's document
#: when no earlier run read it. Past that it lists from what it knows and says
#: ``tools/list_changed`` once the document arrives.
LISTING_GRACE_SECONDS = 1.0

#: Seconds before the installation's document is asked for again after it
#: could not be reached (the next tools/list or tool call asks).
RELEARN_SECONDS = 60.0

#: What each Beakr installation this machine's MCP servers have read leaves
#: out, under the Beakr home, so the next server lists and instructs right at
#: once (B10): ``{base_url: {"unserved": [tool, ...], "read_at": iso}}``.
INSTALLATIONS_FILE = "mcp-installations.json"


def _base_url() -> str | None:
    """The API this server calls (``BEAKR_API_URL``, the config, else production)."""
    from beakr_sdk.credentials import resolve_base_url

    try:
        return resolve_base_url()
    except Exception:
        return None


def _installations_path() -> Path:
    from beakr_sdk.credentials import beakr_home

    return beakr_home() / INSTALLATIONS_FILE


def _installation_key(base_url: str) -> str:
    return base_url.strip().rstrip("/").lower()


def remembered_unserved(base_url: str | None) -> set[str] | None:
    """What the last read of ``base_url``'s document left out, or None if none was saved."""
    if not base_url:
        return None
    try:
        saved = json.loads(_installations_path().read_text())
        entry = saved[_installation_key(base_url)]
        return {str(name) for name in entry["unserved"]}
    except Exception:
        return None


def remember_unserved(base_url: str | None, unserved: set[str]) -> None:
    """Save what ``base_url``'s document leaves out, for the next server. Never raises."""
    if not base_url:
        return
    from beakr_sdk.credentials import write_private_file

    from beakr_cli.config import ensure_dir

    path = _installations_path()
    try:
        saved = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(saved, dict):
            saved = {}
        saved[_installation_key(base_url)] = {
            "unserved": sorted(unserved),
            "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        ensure_dir()
        write_private_file(path, json.dumps(saved, indent=2, sort_keys=True) + "\n")
    except Exception:
        return


class TieredMCPServer(MCPServer):
    """An MCP server whose listing follows the mode and the installation (W8.2).

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
    installation leaves out, before ``initialize`` (or ``server/discover``) is
    answered, so they never send an agent to a tool it cannot see.

    One server answers both protocol eras the MCP library serves: the
    ``initialize`` handshake (2024-11-05 through 2025-11-25) and the
    per-request envelope of 2026-07-28, whose clients start with
    ``server/discover``. The first request a client sends decides which.
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
        # While a client is served (:meth:`serve`), what the installation's
        # document leaves out is read in the background, never before an answer.
        #: The stdio loop's task group, which runs that read; None outside it.
        self._tasks: anyio.abc.TaskGroup | None = None
        #: What the document leaves out, once a read of it came back.
        self._learned: set[str] | None = None
        #: Until then: what an earlier server read for this installation, if any.
        self._remembered: set[str] | None = None
        #: Set when the read in flight ends; None when none is in flight.
        self._learning: anyio.Event | None = None
        self._last_read_started = -math.inf
        #: The ``initialize`` answer's options, whose instructions a read refits.
        self._options: InitializationOptions | None = None
        #: The context of the latest tools/list, to announce a changed listing on.
        self._listing_context: Context | None = None

    def initialization_options(self) -> InitializationOptions:
        """The ``initialize`` answer's options, declaring ``tools.listChanged``."""
        return self._lowlevel_server.create_initialization_options(
            NotificationOptions(tools_changed=True)
        )

    async def run_stdio_async(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.serve(read_stream, write_stream)

    async def serve(self, read_stream: Any, write_stream: Any) -> None:
        """Answer one client on these streams, reading the installation alongside (B10).

        ``initialize`` is answered at once. The installation's document (which
        tools it serves) is read in the background with a short connect
        timeout: before 0.7.1 the server read it first, and an unreachable
        installation held ``initialize`` for 30 to 60 seconds, past Claude
        Code's 30-second start-up limit. Until the read returns, the listing and
        the instructions follow what the last server read for this
        installation, else the whole surface; a read that changes the listing
        sends ``tools/list_changed``.
        """
        from beakr_cli import scope_session

        self._remembered = remembered_unserved(_base_url())
        self._refit_instructions()
        self._options = self.initialization_options()
        # Self-hosted line: the session the person chooses this server's projects for
        # (beakr_cli.scope_session), made here without a request and started in the
        # background, so a session start against an unreachable installation does not
        # hold ``initialize`` either. Until it has started, every tool call waits for
        # it (McpSession.ensure_started): none runs with the person's own credential.
        # A conversation's session is left open on exit: the next server for the same
        # conversation joins it again.
        session = scope_session.prepare_for_mcp()
        try:
            async with anyio.create_task_group() as tasks:
                self._tasks = tasks
                if session is not None:
                    # Returns once the start holds the session's lock (no request yet).
                    await tasks.start(self._start_session, session)
                self._read_installation_soon()
                await self._lowlevel_server.run(read_stream, write_stream, self._options)
                tasks.cancel_scope.cancel()
        finally:
            self._tasks = None
            await scope_session.release_for_mcp()

    async def _start_session(
        self, session: Any, *, task_status: anyio.abc.TaskStatus[None] = anyio.TASK_STATUS_IGNORED
    ) -> None:
        """Start the server's session in :meth:`serve`'s background (self-hosted line).

        ``task_status`` is told once the start holds the session's lock, before any
        request, so a tool call that comes first waits for this start. An
        installation without sessions takes the session's tools out of the listing.
        """
        from beakr_cli import scope_session

        await scope_session.start_prepared_for_mcp(session, ready=task_status.started)
        if scope_session.ACTIVE is None:
            await self._listing_changed(self._listing_context)

    def _known_unserved(self) -> set[str]:
        """What the installation leaves out, as far as this server knows yet."""
        if self._learned is not None:
            return self._learned
        return self._remembered or set()

    def _refit_instructions(self) -> None:
        """Instructions naming only the tools listed now, in every answer still to come."""
        if self._instructions_for is None:
            return
        text = self._instructions_for(self._hidden | self._known_unserved())
        self._lowlevel_server.instructions = text
        if self._options is not None:
            # The ``initialize`` answer reads its options when it is sent.
            self._options.instructions = text

    def _read_installation_soon(self) -> None:
        """Start reading the installation's document, unless read, in flight or just tried."""
        if self._tasks is None or self._learned is not None or self._learning is not None:
            return
        now = time.monotonic()
        if now - self._last_read_started < RELEARN_SECONDS:
            return
        self._last_read_started = now
        self._learning = anyio.Event()
        self._tasks.start_soon(self._read_installation, self._learning)

    async def _read_installation(self, done: anyio.Event) -> None:
        try:
            names = [tool.name for tool in await super().list_tools()]
            unserved = await self._read_unserved(names)
            if unserved is None:
                # Unreachable: keep listing what is known; asked again later.
                return
            before = self._hidden | self._known_unserved()
            self._learned = unserved
            remember_unserved(_base_url(), unserved)
            self._refit_instructions()
            if self._hidden | unserved != before:
                await self._listing_changed(self._listing_context)
        finally:
            self._learning = None
            done.set()

    async def _listed_unserved(self, names: list[str]) -> set[str]:
        """What the listing leaves out for the installation, without waiting on it.

        Outside :meth:`serve` (a direct call, a test) nobody waits on a
        handshake, so the document is read now, as before.
        """
        if self._tasks is None:
            return await self._unserved(names)
        self._read_installation_soon()
        learning = self._learning
        if self._learned is None and self._remembered is None and learning is not None:
            with anyio.move_on_after(LISTING_GRACE_SECONDS):
                await learning.wait()
        return self._known_unserved()

    async def fit_instructions(self) -> None:
        """Rebuild the instructions without the tools this installation does not list."""
        if self._instructions_for is None:
            return
        names = [tool.name for tool in await super().list_tools()]
        hidden = self._hidden | await self._unserved(names)
        self._lowlevel_server.instructions = self._instructions_for(hidden)

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

    def get_context(self) -> Context:
        """The context of the tool call in progress.

        Outside a call it is a context with no request behind it, whose
        ``session`` and ``request_context`` raise ``ValueError``, as FastMCP's
        did; callers use that to tell they are not answering a client.
        """
        current = _CALL_CONTEXT.get()
        if current is not None:
            return current
        return Context(mcp_server=self, subscriptions=self._subscriptions)

    async def _handle_list_tools(self, ctx: Any, params: Any) -> Any:
        # Kept so a listing the background read changes can be announced.
        self._listing_context = Context(
            request_context=ctx, mcp_server=self, subscriptions=self._subscriptions
        )
        return await super()._handle_list_tools(ctx, params)

    async def _listing_changed(self, context: Context | None = None) -> None:
        """Tell the client to read the listing again.

        On the connection of the request in progress, or of ``context`` (the
        latest tools/list, for a change found in the background; with none, no
        listing was sent yet and the first one will be right).

        A handshake-era client hears ``notifications/tools/list_changed`` on
        its connection. A 2026-07-28 client hears it only on a
        ``subscriptions/listen`` stream it opened, so the change is published
        on the server's subscription bus, which feeds those streams; the
        library drops a bare copy on the shared channel at that version.
        """
        if context is None:
            context = self.get_context()
        try:
            session = context.session
        except ValueError:
            return
        try:
            if context.protocol_version in MODERN_PROTOCOL_VERSIONS:
                await context.notify_tools_changed()
            else:
                await session.send_tool_list_changed()
        except Exception:
            # The listing is re-read on the next tools/list anyway; a client
            # that went away must not turn a tool result into an error.
            return

    async def _unserved(self, names: list[str]) -> set[str]:
        """Tools whose documented operations this installation's document leaves out."""
        return await self._read_unserved(names) or set()

    async def _read_unserved(self, names: list[str]) -> set[str] | None:
        """:meth:`_unserved`, or None when the installation could not be reached."""
        from beakr_sdk.capabilities import Capabilities
        from beakr_sdk.errors import APIConnectionError

        from beakr_cli.capabilities import unserved_tools
        from beakr_cli.sdk import get_async_sdk

        try:
            async with get_async_sdk(timeout=10.0) as sdk:
                document = await sdk.meta.openapi()
        except APIConnectionError:
            return None
        except Exception:
            # No credential or configuration yet, or no document: list everything.
            return set()
        capabilities = Capabilities(document if isinstance(document, Mapping) else None)
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
        from beakr_cli import scope_session
        from beakr_cli.mcp_session import SESSION_TOOLS

        order = {name: index for index, name in enumerate(stdio_tools())}
        tools = sorted(await super().list_tools(), key=lambda t: order.get(t.name, len(order)))
        # The session's own tools are listed only while the server holds a session.
        session_tools = [t for t in tools if t.name in SESSION_TOOLS]
        tools = [t for t in tools if t.name not in SESSION_TOOLS]
        if scope_session.ACTIVE is None:
            session_tools = []
        if _project_pinned():
            tools = [
                without_project_parameters(tool) if tool.name in PINNABLE_TOOLS else tool
                for tool in tools
            ]
        tools = [
            tool.model_copy(update={"input_schema": listed_schema(tool.name, tool.input_schema)})
            if tool.name in confirm_lock_tools()
            else tool
            for tool in tools
        ]
        hidden = self._hidden | await self._listed_unserved([tool.name for tool in tools])
        tools = [tool for tool in tools if tool.name not in hidden]
        if lists_every_tool():
            return tools + session_tools
        listed = set(eager_tools()) | self._revealed
        return [tool for tool in tools if tool.name in listed] + session_tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> Any:
        """Run a tool with forgiving arguments, and return any error compactly.

        Top-level Knowledge options and common aliases are normalized first
        (``mcp_call_shaping``). An argument the tool does not take is then
        refused, as the hosted server's schema check refuses it, rather than
        dropped: a dropped ``scope`` read everything the caller can see. A
        validation failure becomes one line naming the fix; an API refusal, a
        project that names nothing and an error outcome become ``{status,
        error_code, message, fix}`` marked ``isError``.

        ``context`` is the request's, passed by the library for a client's
        tools/call; a direct call (tests, the engine's contract gate) runs
        without one, as outside a request.
        """
        from beakr_cli import lock_confirm, session_notice
        from beakr_cli.sdk import MCP_CLIENT_AGENT

        # confirm_lock enters the locked project a held call named: taken off the
        # call before anything checks its arguments, and sent on every request it
        # makes (X-Beakr-Confirm-Lock), as the hosted server takes it.
        try:
            arguments, confirmed = lock_confirm.take(
                name, dict(arguments or {}), confirm_lock_tools()
            )
        except lock_confirm.InvalidConfirmLock as exc:
            return _error_result(
                {
                    "status": "error",
                    "error_code": "invalid_arguments",
                    "message": str(exc),
                    "fix": "Pass the project id a session_lock_confirm answer named, as "
                    "confirm_lock, or leave it out.",
                }
            )
        if context is None:
            context = Context(mcp_server=self, subscriptions=self._subscriptions)
        # A document that could not be read at start-up is asked for again.
        self._read_installation_soon()
        call_context = _CALL_CONTEXT.set(context)
        # Writes are attributed to the agent that made them: the MCP client's
        # clientInfo, unless BEAKR_AGENT names one (sdk._agent).
        agent = self._client_agent()
        token = MCP_CLIENT_AGENT.set(agent)
        try:
            # A request that locked the session says so on the result's first line,
            # in the API's words, as the hosted server does.
            with session_notice.collecting() as notices, lock_confirm.confirming(confirmed):
                result = await self._gated_call(name, arguments, agent)
            return _with_notices(result, notices)
        except ToolError as exc:
            # A raised error leaves as its message (the MCP library sends
            # ``str(exc)`` as the reply's text), so it is shown the same way.
            from beakr_cli.mcp_render import visible_controls

            message = str(exc)
            shown = visible_controls(message)
            if shown == message:
                raise
            raise ToolError(shown) from exc
        finally:
            MCP_CLIENT_AGENT.reset(token)
            _CALL_CONTEXT.reset(call_context)

    async def _gated_call(self, name: str, arguments: dict[str, Any], agent: str | None) -> Any:
        from beakr_cli import mcp_session, scope_session

        # Codex names its thread on every call: work in that conversation's session,
        # the one its shell commands use (decided before the first call runs).
        thread = scope_session.codex_thread_id(self._request_meta())
        if thread:
            await scope_session.adopt_conversation(thread)
        if scope_session.ACTIVE is not None:
            scope_session.ACTIVE.calls += 1
        # Until the person chooses this session's projects, answer with the
        # picker (in chat where the client can show a form) instead.
        refusal = await mcp_session.gate(self, name, agent)
        if refusal is not None:
            return _error_result(refusal)
        result = await self._call_tool(name, arguments)
        if not mcp_session.was_replaced():
            return result
        # The person ended the session while this call ran: the server started a
        # pending one in its place. Ask again, as at startup, then run it once more.
        refusal = await mcp_session.gate(self, name, agent)
        if refusal is not None:
            return _error_result(refusal)
        return await self._call_tool(name, arguments)

    def _request_meta(self) -> Any:
        """The running request's ``_meta``, when there is one."""
        try:
            return self.get_context().request_context.meta
        except (ValueError, AttributeError, LookupError):
            return None

    def _client_agent(self) -> str | None:
        """``name/version`` of the client, when a request is running.

        A handshake-era client names itself in ``initialize``; a 2026-07-28
        client in each request's ``_meta``, which the library reads into the
        same ``client_params``.
        """
        from beakr_cli.sdk import client_agent

        try:
            params = self.get_context().session.client_params
        except ValueError:
            return None
        info = getattr(params, "client_info", None)
        if info is None:
            return None
        return client_agent(info.name, info.version)

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        from beakr_sdk.errors import APIConnectionError

        from beakr_cli import mcp_call_shaping
        from beakr_cli.capabilities import NOT_PROVIDED_CODE
        from beakr_cli.mcp_errors import ApiCallError
        from beakr_cli.mcp_tools_platform import ScopeNotFound
        from beakr_cli.scope_session import SessionScopeError

        tool = self._tool_manager.get_tool(name)
        schema = tool.parameters if tool is not None else {}
        declared = set((schema.get("properties") or {}).keys())
        given = dict(arguments or {})
        try:
            normalized = mcp_call_shaping.normalize_arguments(name, given, declared)
        except mcp_call_shaping.ConflictingArguments as exc:
            # Refused like any other bad call, as compact JSON naming the fix.
            return _error_result(
                {
                    "status": "error",
                    "error_code": "invalid_arguments",
                    "message": str(exc),
                    "fix": exc.fix,
                }
            )
        if tool is not None:
            refusal = mcp_call_shaping.unknown_arguments(name, normalized, schema)
            if refusal is not None:
                return _error_result(refusal)
        try:
            # The tool's own dict (``mcp_result.finish`` shape), not the
            # library's conversion of it, which would serialize the whole dict
            # as JSON.
            result = await self._tool_manager.call_tool(
                name, normalized, self.get_context(), convert_result=False
            )
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, ValidationError):
                raise ToolError(mcp_call_shaping.validation_message(name, cause, schema)) from cause
            if isinstance(cause, ScopeNotFound):
                return _error_result(
                    mcp_call_shaping.compact_error(name, {**cause.fields, "status": "error"})
                )
            if isinstance(cause, SessionScopeError):
                if isinstance(cause.__cause__, APIConnectionError):
                    # The session could not start: the installation can't be reached.
                    return _error_result(unreachable_error(name, cause.__cause__))
                return _error_result(
                    mcp_call_shaping.compact_error(name, {**cause.fields, "status": "error"})
                )
            if isinstance(cause, ApiCallError):
                if cause.fields.get("error_code") == NOT_PROVIDED_CODE:
                    await self.hide(name)
                return _error_result(
                    mcp_call_shaping.compact_error(name, {**cause.fields, "status": "error"})
                )
            if isinstance(cause, APIConnectionError):
                return _error_result(unreachable_error(name, cause))
            if isinstance(exc, UnexpectedToolError) and cause is not None:
                # mcp 2 withholds an unanticipated exception's text ("Error
                # executing tool <name>"). This server runs on the person's own
                # machine for their own agent, and that text is what says what
                # to do: "not signed in, run beakr auth login", a refused
                # connection to the configured API URL. It is sent as before.
                raise ToolError(f"Error executing tool {name}: {cause}") from cause
            raise
        body = result if isinstance(result, dict) else {"content": result}
        if body.get("status") == "error":
            return _error_result(mcp_call_shaping.compact_error(name, body))
        from beakr_cli.mcp_render import render

        return CallToolResult(content=[_text_content(render(name, body))])


def _text_content(text: str) -> TextContent:
    """The one text block a tool call answers with, as it leaves this server.

    Every reply and every error result passes through here, so this is where a
    control, bidi or zero-width character in anything a tool printed (a title,
    a file's text, a connected service's reply, an echoed argument) is shown as
    a visible escape and never reaches a client's terminal as it was stored.
    The hosted server's ``_text_content`` does the same with the same function
    (``mcp_render.visible_controls``), so both servers send the same text.
    """
    from beakr_cli.mcp_render import visible_controls

    return TextContent(type="text", text=visible_controls(text))


def _with_notices(result: Any, notices: list[str]) -> Any:
    """``result`` with each lock notice the call heard above its text (``Session: ...``)."""
    from beakr_cli.session_notice import prefixed

    if not notices or not isinstance(result, CallToolResult):
        return result
    # The notices lead, and the whole text leaves through _text_content, as on the
    # hosted server (its escape changes nothing already escaped).
    content = list(result.content)
    if content and isinstance(content[0], TextContent):
        content[0] = _text_content(prefixed(content[0].text, notices))
    else:
        content.insert(0, _text_content(prefixed("", notices)))
    return result.model_copy(update={"content": content})


#: ``error_code`` of a call that never reached the installation.
UNREACHABLE_CODE = "unreachable"
#: ``error_code`` of a call whose connection opened and then failed or went quiet.
NO_ANSWER_CODE = "no_answer"


def unreachable_error(tool: str, exc: BaseException) -> dict[str, Any]:
    """``{status, error_code, message, fix}`` for a call the API never answered (B10).

    The SDK raises ``APIConnectionError`` with the transport's error as its
    cause. A connection that could not be opened (refused, a name that does
    not resolve, a connect that timed out) is ``unreachable``; one that opened
    and then failed or went quiet is ``no_answer``.
    """
    import httpx

    from beakr_cli.sdk import connect_timeout

    url = _base_url() or "the configured API URL"
    cause = exc.__cause__
    if isinstance(cause, httpx.ConnectTimeout):
        reason = f"no connection within {connect_timeout():g} seconds"
    elif isinstance(cause, (httpx.ConnectError, httpx.ProxyError)):
        reason = str(cause) or "the connection was refused"
    else:
        reason = None
    if reason is not None:
        return {
            "status": "error",
            "error_code": UNREACHABLE_CODE,
            "message": f"Can't reach the Beakr installation at {url} ({reason}).",
            "fix": (
                "Check that the network path to it is up (VPN, proxy, firewall) and that "
                "the API URL is right (BEAKR_API_URL, or `beakr auth status`), then call "
                f"{tool} again."
            ),
        }
    detail = str(cause or exc) or type(cause or exc).__name__
    return {
        "status": "error",
        "error_code": NO_ANSWER_CODE,
        "message": f"The Beakr installation at {url} did not answer ({detail}).",
        "fix": f"Call {tool} again; if it keeps failing, check that the installation is up.",
    }


def _error_result(body: dict[str, Any]) -> CallToolResult:
    """An error, once, as compact JSON text: ``{status, error_code, message, fix}``."""
    # JSON already escapes everything but DEL, which ``visible_controls`` shows.
    return CallToolResult(
        content=[_text_content(json.dumps(body, separators=(", ", ": ")))],
        is_error=True,
    )
