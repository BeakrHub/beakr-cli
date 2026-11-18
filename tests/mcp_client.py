"""Reach the stdio MCP server the way a client does: one MCP session, in process.

mcp 1 let a test call the low-level ``tools/call`` handler with a bare request;
mcp 2 hands every handler the request's context, which only a session builds.
These helpers open a real session over the library's in-memory transport, so a
test sees exactly the reply a client receives, after the library's own error
handling, and no test reaches into the library's private handler tables.

``mode`` is the client's: ``"legacy"`` sends ``initialize`` (the handshake
protocol versions every client spoke before 2026-07-28); ``"2026-07-28"`` uses
the per-request envelope that protocol version's clients send instead.
"""

from __future__ import annotations

from typing import Any

from mcp import Client, types

from beakr_cli.mcp_server import mcp

#: Both protocol eras the server answers.
MODES = ("legacy", "2026-07-28")


def client(*, mode: str = "legacy", client_info: types.Implementation | None = None) -> Client:
    """An unentered client of the stdio server; its response cache is off."""
    return Client(mcp, mode=mode, client_info=client_info, cache=None)


async def call_tool(
    name: str, arguments: dict[str, Any] | None = None, *, mode: str = "legacy"
) -> types.CallToolResult:
    """One ``tools/call`` in its own session, answered as a client receives it."""
    async with client(mode=mode) as session:
        return await session.call_tool(name, arguments or {})


async def list_tools(*, mode: str = "legacy") -> list[types.Tool]:
    """The ``tools/list`` answer a client receives."""
    async with client(mode=mode) as session:
        return (await session.list_tools()).tools
