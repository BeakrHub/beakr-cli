"""Profiles: the caller's inferred profile claims and the project knowledge profiles.

Every route here is unlisted.

Example::

    for claim in beakr.profile.claims():
        print(claim["layer"], claim["content"], claim["confirmation_state"])
    beakr.profile.confirm(claim_id)
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource, as_list

from beakr_cli._private_api._generated import operations as ops


class Profile(Resource[M]):
    def claims(
        self, *, include_inactive: bool | None = None, layer: list[Any] | None = None
    ) -> Any:
        """The evidence-backed profile the agents use, each claim with its ``profile_layer``.

        ``layer`` keeps only the named layers: profile layers (``identity``,
        ``goals``, ``arcs``, ``reminders``, ``preferences``, ``patterns``) or the
        stored layers they cover (``care_about``, ``current_arc``, ...).
        """
        return self._run_then(
            ops.build_personal_memory_list_user_profile_claims(
                include_inactive=include_inactive, layer=list(layer) if layer else None
            ),
            lambda r: as_list(r, "claims", "data"),
        )

    def _patch(self, claim_id: str, body: dict[str, Any]) -> Any:
        return self._run(ops.build_personal_memory_update_user_profile_claim(claim_id, body=body))

    def confirm(self, claim_id: str) -> Any:
        return self._patch(claim_id, {"confirm": True})

    def dismiss(self, claim_id: str) -> Any:
        """Mark a claim wrong; it stays visible as dismissed and is not relearned."""
        return self._patch(claim_id, {"dismiss": True})

    def restore(self, claim_id: str) -> Any:
        return self._patch(claim_id, {"restore": True})

    def edit(
        self,
        claim_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        next_step: str | None = None,
    ) -> Any:
        body = clean_body({"content": content, "status": status, "next_step": next_step})
        return self._patch(claim_id, body)

    @returns(ops.build_personal_memory_apply_user_profile_changes)
    def apply(self, changes: list[dict[str, Any]]) -> Any:
        """Apply several claim changes at once, all or nothing.

        Each change is ``{"claim_id", "action": "confirm"|"dismiss"|"edit"|"restore"}``
        plus, for ``edit``, the fields to change. The first refused change leaves
        every claim as it was.
        """
        return self._run(
            ops.build_personal_memory_apply_user_profile_changes(body={"changes": changes})
        )

    @returns(ops.build_personal_memory_forget_user_profile_claim)
    def forget(self, claim_id: str) -> Any:
        """Erase a claim permanently (and keep it from being relearned)."""
        return self._run(ops.build_personal_memory_forget_user_profile_claim(claim_id))

    @returns(ops.build_knowledge_get_project_profile)
    def project(self, project_id: str) -> Any:
        """A project's knowledge profile, or None."""
        return self._run(ops.build_knowledge_get_project_profile(project_id))

    def create_profile(self, *, project_id: str, **fields: Any) -> Any:
        """Start a project's knowledge profile as a project administrator."""
        if not project_id:
            raise ValueError("Knowledge Profiles require a project_id")
        body = clean_body({**fields, "profile_type": "project", "project_id": project_id})
        return self._run(ops.build_knowledge_create_profile(body=body))
