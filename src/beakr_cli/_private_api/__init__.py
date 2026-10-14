"""The CLI's private client for the unlisted Beakr API operations it calls.

The public SDK (beakr-sdk) covers only the documented contract. The CLI and its
stdio MCP server also sign in and run commands and tools over a few routes the
API serves without documenting them ("unlisted": no stability promise). Those
live here, and only those:

- ``operations.allowlist``: the reviewed list of unlisted operations the CLI
  calls, one per line.
- :mod:`beakr_cli._private_api._generated`: a request builder per allowlisted
  operation, rendered from the engine's ``internal-openapi.json`` by
  ``scripts/generate_private_api.py`` with the SDK's own generator.
- :mod:`beakr_cli._private_api.resources`: the CLI's methods over them, each
  subclassing the public resource it extends where there is one.
- :class:`PrivateBeakr` / :class:`AsyncPrivateBeakr`: the SDK clients with all of
  it attached (``beakr_cli.sdk`` builds them).

``tests/test_private_api_allowlist.py`` keeps the allowlist and the call sites
equal.
"""

from __future__ import annotations

from beakr_sdk._replay import register_replaying_creates

from beakr_cli._private_api.client import AsyncPrivateBeakr, PrivateBeakr

#: Allowlisted POSTs the engine replays on a resend with the same
#: ``Idempotency-Key`` (its ``tests/api/test_public_create_idempotency.py``
#: classifies them; ``tests/test_private_api_allowlist.py`` compares). The SDK
#: lists only documented creates, so the client resends these after an
#: ambiguous failure only once they are registered.
REPLAYING_CREATES: tuple[str, ...] = (
    "/v1/interview/requests",
    "/v1/invitations",
    "/v1/knowledge/profiles",
    "/v1/workflows/configs",
    "/v1/workflows/configs/{config_id}/run",
)

register_replaying_creates(REPLAYING_CREATES)

__all__ = ["REPLAYING_CREATES", "AsyncPrivateBeakr", "PrivateBeakr"]
