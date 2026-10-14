"""The CLI's resources over unlisted routes, attached by :func:`attach_private_resources`.

Each module either holds a whole resource the public SDK does not have
(``profile``, ``skills``, ``memories``...) or subclasses the public resource of
the same name with the unlisted methods the CLI calls (``files.Files`` adds
``versions`` and the extraction reads). Only methods with a call site in the
CLI or its stdio MCP server live here.
"""

from __future__ import annotations

from typing import Any

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

__all__ = ["attach_private_resources"]


def attach_private_resources(client: Any) -> None:
    """Replace the public resources the CLI extends and add the ones it alone uses.

    Runs after the SDK attached its public resources, so the ones not replaced
    here (keys, jobs, runs, members, engine...) stay as they are.
    """
    # Public resources, extended with the unlisted methods the CLI calls.
    client.conversations = Conversations(client)
    client.files = Files(client)
    client.integrations = Integrations(client)
    client.interviews = Interviews(client)
    client.knowledge = Knowledge(client)
    client.org = OrgAdmin(client)
    # Resources over unlisted routes only.
    client.auth = Auth(client)
    client.invitations = Invitations(client)
    client.memories = Memories(client)
    client.outputs = Outputs(client)
    client.profile = Profile(client)
    client.skills = Skills(client)
    client.usage = Usage(client)
    client.trace = Trace(client)
    client.workflows = Workflows(client)
