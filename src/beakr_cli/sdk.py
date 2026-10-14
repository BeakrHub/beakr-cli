"""The SDK clients the CLI and its MCP server use, identified as the CLI.

Every request the CLI makes to the Beakr API goes through ``beakr_sdk``
(developer platform W7: the CLI is rebuilt on the Python SDK), so credentials,
refresh, retries, the error envelope and attribution headers behave the same
in the terminal, in the stdio MCP server and in a customer's own SDK script.

They are the CLI's private clients (:mod:`beakr_cli._private_api`): the SDK's
``Beakr`` covers only the documented contract, while the CLI also signs in and
runs commands and MCP tools over a few routes that are reachable but not
documented (profile, skills, memory, outputs, workflows...), each listed in
``_private_api/operations.allowlist``.
"""

from __future__ import annotations

import os
import uuid

from beakr_cli import __version__
from beakr_cli._private_api import AsyncPrivateBeakr, PrivateBeakr

CLI_CLIENT_NAME = f"beakr-cli/{__version__}"

#: ``X-Beakr-Client`` for requests from the MCP server, so the API can tell
#: agent traffic from a person at the terminal.
MCP_CLIENT_NAME = f"beakr-mcp/{__version__}"

#: The run label of this MCP server process: every Knowledge write it files
#: carries it, so one agent session's writes can be undone together.
MCP_RUN_LABEL = f"stdio:{uuid.uuid4().hex}"

#: The version lets the API tell which clients are still on an old release.
_CLI_HEADERS = {"User-Agent": f"beakr-cli/{__version__}"}


def _agent() -> str | None:
    return os.environ.get("BEAKR_AGENT") or None


def get_sdk(
    *, client_name: str | None = None, timeout: float = 60.0, base_url: str | None = None
) -> PrivateBeakr:
    """A client sending ``X-Beakr-Client: beakr-cli/<version>`` (or ``client_name``).

    ``BEAKR_AGENT`` is forwarded as ``X-Beakr-Agent`` so a script or hook can
    attribute its writes to the agent session that ran it, and ``BEAKR_RUN`` as
    ``X-Beakr-Run`` so `beakr kb undo-run <run>` can take back one run's writes.
    ``base_url`` overrides the configured API (``BEAKR_BASE_URL``, the config).
    """
    return PrivateBeakr(
        base_url=base_url,
        client_name=client_name or CLI_CLIENT_NAME,
        agent=_agent(),
        run=os.environ.get("BEAKR_RUN") or None,
        timeout=timeout,
        default_headers=_CLI_HEADERS,
    )


def get_async_sdk(
    *, client_name: str = MCP_CLIENT_NAME, timeout: float = 170.0
) -> AsyncPrivateBeakr:
    """The async client for the stdio MCP server.

    Both clients wait past the research route limit because the engine gives research
    (an agentic loop that downloads and reads documents from connected
    services) up to 150s; 170s outwaits it, as the SDK's research call does
    (``beakr_sdk.resources.knowledge.RESEARCH_TIMEOUT``). Writes carry
    ``BEAKR_RUN`` when a script declares one, else this process's run label.
    """
    run = os.environ.get("BEAKR_RUN") or (MCP_RUN_LABEL if client_name == MCP_CLIENT_NAME else None)
    return AsyncPrivateBeakr(
        client_name=client_name,
        agent=_agent(),
        run=run,
        timeout=timeout,
        default_headers=_CLI_HEADERS,
    )
