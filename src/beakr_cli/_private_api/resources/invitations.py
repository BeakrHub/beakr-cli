"""Organization invitations, over unlisted routes."""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource

from beakr_cli._private_api._generated import operations as ops


class Invitations(Resource[M]):
    @returns_all(ops.build_invitations_list_pending_invitations)
    def list(self) -> Any:
        return self._list_all(ops.build_invitations_list_pending_invitations())

    def create(
        self,
        email: str,
        *,
        role: str = "member",
        expires_in_days: int | None = None,
        project_id: str | None = None,
    ) -> Any:
        """Invite someone to the organization (or, with ``project_id``, to one project)."""
        body = clean_body(
            {
                "invitee_email": email,
                "role": role,
                "expires_in_days": expires_in_days,
                "resource_id": project_id,
            }
        )
        return self._run(ops.build_invitations_create_invitation(body=body))

    @returns(ops.build_invitations_revoke_invitation)
    def revoke(self, invitation_id: str) -> Any:
        return self._run(ops.build_invitations_revoke_invitation(invitation_id))
