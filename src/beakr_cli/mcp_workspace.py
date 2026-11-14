"""Which Beakr this server's calls land in: the environment and the organization.

Root cause (agent tester report, 2026-11-13): neither MCP server's
``beakr_version`` nor ``context`` said which deployment answered or which
organization the calls acted in, so an agent connected to sandbox, or to the
wrong organization, read an empty Knowledge as "nothing is known" and could
not tell. The hosted server now leads both with one sentence
(``Connected to: Beakr <environment>, organization "<name>" (slug <slug>).``)
and this server prints the same one, from what the API says:

- ``context`` reads ``workspace`` off the reply of ``POST /v1/context`` it
  already has (:func:`workspace_prefix`).
- ``beakr_version`` calls no route of its own otherwise, so it asks
  (:func:`connected_line`): the organization from ``GET
  /v1/organizations/me/active``, which needs no scope, as the hosted tool does,
  and the environment from ``workspace.environment`` on the context route.
  The hosted tool reads the environment from its own settings; the API
  publishes that name only there (``GET /health`` gives the raw setting, where
  a developer's machine is ``development``, not ``local``).

A read that fails drops what it would have named, and with nothing known the
line is left out: saying where the calls land must never fail the tool, nor
hold it up (the two reads share :data:`_READ_SECONDS`).

``workspace_line`` is the engine's (``beakr/services/workspace_identity.py``),
byte for byte from that function's ``def`` to the end of this file;
``tests/test_mcp_workspace_line.py`` fails the moment the copy drifts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from beakr_cli.mcp_errors import call_api

#: The smallest context the route builds (``ContextIn.budget_chars``): only its
#: ``workspace`` is read.
_SMALLEST_CONTEXT = 2_000
#: How long ``beakr_version`` waits to learn where it is connected. An API that
#: does not answer must not keep the installed version from being reported.
_READ_SECONDS = 10.0


def workspace_prefix(data: Any) -> str:
    """``Connected to: ...`` and a blank line, when the context route said where it landed."""
    where = data.get("workspace") if isinstance(data, dict) else None
    if not isinstance(where, dict):
        return ""
    line = workspace_line(where.get("environment"), where.get("organization"))
    return f"{line}\n\n"


async def connected_line() -> str:
    """The ``Connected to: ...`` sentence for ``beakr_version``, or "" when nothing is known."""
    try:
        organization, environment = await asyncio.wait_for(
            asyncio.gather(_acting_organization(), _environment()), timeout=_READ_SECONDS
        )
    except asyncio.TimeoutError:
        return ""
    if not organization and not environment:
        return ""
    return workspace_line(environment, organization)


async def _acting_organization() -> dict[str, str] | None:
    """The organization these calls act in; None when it cannot be read."""
    try:
        data = await call_api(lambda sdk: sdk.me.active_org())
    except Exception:
        # Not signed in, the API unreachable, a refusal: the version is still
        # worth reporting when the organization is not.
        return None
    if not isinstance(data, dict) or not (data.get("name") or data.get("slug")):
        return None
    return {"name": str(data.get("name") or ""), "slug": str(data.get("slug") or "")}


async def _environment() -> str | None:
    """The deployment's name as the API gives it (production, sandbox, local, ...), or None."""
    try:
        data = await call_api(
            lambda sdk: sdk.engine.context(format="json", budget_chars=_SMALLEST_CONTEXT)
        )
    except Exception:
        # An older API, a credential without the context scope, no connection.
        return None
    where = data.get("workspace") if isinstance(data, dict) else None
    environment = where.get("environment") if isinstance(where, dict) else None
    return str(environment).strip() or None if isinstance(environment, str) else None


def workspace_line(environment: str | None, organization: Mapping[str, Any] | None) -> str:
    """One sentence saying where a call landed, from what is known of it."""
    place = f"Beakr {environment}" if environment else "Beakr"
    name = str((organization or {}).get("name") or "").strip()
    slug = str((organization or {}).get("slug") or "").strip()
    if name and slug:
        return f'Connected to: {place}, organization "{name}" (slug {slug}).'
    if name or slug:
        return f'Connected to: {place}, organization "{name or slug}".'
    return f"Connected to: {place}."
