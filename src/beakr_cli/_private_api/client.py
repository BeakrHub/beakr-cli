"""The CLI's clients: the public SDK clients plus the unlisted routes the CLI calls.

:class:`PrivateBeakr` and :class:`AsyncPrivateBeakr` are
:class:`beakr_sdk.Beakr` and :class:`beakr_sdk.AsyncBeakr` with the resources of
:mod:`beakr_cli._private_api.resources` attached, and ``private_api``: one
generated method per allowlisted unlisted operation. ``api`` stays the public
SDK's, the documented contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from beakr_sdk import AsyncBeakr, Beakr

from beakr_cli._private_api._generated.operations import AsyncBeakrApi, BeakrApi
from beakr_cli._private_api.resources import attach_private_resources

if TYPE_CHECKING:
    from beakr_sdk._typing import Async, Sync

    from beakr_cli._private_api.resources.auth import Auth
    from beakr_cli._private_api.resources.conversations import Conversations
    from beakr_cli._private_api.resources.files import Files
    from beakr_cli._private_api.resources.integrations import Integrations
    from beakr_cli._private_api.resources.interviews import Interviews
    from beakr_cli._private_api.resources.invitations import Invitations
    from beakr_cli._private_api.resources.knowledge import Knowledge
    from beakr_cli._private_api.resources.memories import Memories
    from beakr_cli._private_api.resources.org_admin import OrgAdmin
    from beakr_cli._private_api.resources.outputs import Outputs
    from beakr_cli._private_api.resources.profile import Profile
    from beakr_cli._private_api.resources.skills import Skills
    from beakr_cli._private_api.resources.usage import Trace, Usage
    from beakr_cli._private_api.resources.workflows import Workflows


class PrivateBeakr(Beakr):
    """:class:`beakr_sdk.Beakr` plus the unlisted routes the CLI calls."""

    private_api: BeakrApi
    auth: Auth[Sync]
    conversations: Conversations[Sync]
    files: Files[Sync]
    integrations: Integrations[Sync]
    interviews: Interviews[Sync]
    invitations: Invitations[Sync]
    knowledge: Knowledge[Sync]
    memories: Memories[Sync]
    org: OrgAdmin[Sync]
    outputs: Outputs[Sync]
    profile: Profile[Sync]
    skills: Skills[Sync]
    trace: Trace[Sync]
    usage: Usage[Sync]
    workflows: Workflows[Sync]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        attach_private_resources(self)
        self.private_api = BeakrApi(self)

    def __enter__(self) -> PrivateBeakr:
        return self


class AsyncPrivateBeakr(AsyncBeakr):
    """:class:`beakr_sdk.AsyncBeakr` plus the unlisted routes the CLI calls."""

    private_api: AsyncBeakrApi
    auth: Auth[Async]
    conversations: Conversations[Async]
    files: Files[Async]
    integrations: Integrations[Async]
    interviews: Interviews[Async]
    invitations: Invitations[Async]
    knowledge: Knowledge[Async]
    memories: Memories[Async]
    org: OrgAdmin[Async]
    outputs: Outputs[Async]
    profile: Profile[Async]
    skills: Skills[Async]
    trace: Trace[Async]
    usage: Usage[Async]
    workflows: Workflows[Async]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        attach_private_resources(self)
        self.private_api = AsyncBeakrApi(self)

    async def __aenter__(self) -> AsyncPrivateBeakr:
        return self
