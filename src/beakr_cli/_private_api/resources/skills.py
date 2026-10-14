"""Skills: the caller's library, SKILL.md bundles and mined proposals, over unlisted routes.

Example::

    for skill in beakr.skills.list():
        print(skill["slug"], skill["description"])
    files = beakr.skills.bundle(skill_id)["files"]
"""

from __future__ import annotations

import hashlib
from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource

from beakr_cli._private_api._generated import operations as ops

SKILL_MD = "SKILL.md"


class SkillProposals(Resource[M]):
    """Skills Beakr mined from your work, waiting for you to keep or dismiss."""

    @returns_all(ops.build_skills_list_mined_skill_proposals)
    def list(self, *, status: str | None = None) -> Any:
        return self._list_all(ops.build_skills_list_mined_skill_proposals(status=status))

    @returns(ops.build_skills_accept_mined_skill_proposal)
    def accept(self, proposal_id: str, *, force: bool | None = None) -> Any:
        """Install the proposed skill (``force`` replaces a skill with the same slug)."""
        return self._run(ops.build_skills_accept_mined_skill_proposal(proposal_id, force=force))


class Skills(Resource[M]):
    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.proposals: SkillProposals[M] = SkillProposals(client)

    @returns_all(ops.build_skills_list_library_skills)
    def list(self, *, include_content: bool | None = None) -> Any:
        """Skills installed in the caller's library (metadata unless ``include_content``)."""
        return self._list_all(ops.build_skills_list_library_skills(include_content=include_content))

    @returns(ops.build_skills_get_skill_bundle)
    def bundle(self, skill_id: str) -> Any:
        """The SKILL.md folder: ``{skill_id, slug, files: {path: text}, file_count}``."""
        return self._run(ops.build_skills_get_skill_bundle(skill_id))

    @returns(ops.build_skills_replace_skill_bundle)
    def replace_bundle(
        self,
        skill_id: str,
        files: dict[str, str],
        *,
        replace: bool = True,
        expected_sha256: str | None = None,
        force: bool = False,
    ) -> Any:
        """Write the SKILL.md folder of a skill the caller owns (``replace=False`` overlays).

        Pass ``expected_sha256`` (the ``files_sha256`` of the bundle you read, see
        :func:`bundle_sha256`) and a write made after your read is refused with
        ``ConflictError`` (``skill_conflict``) instead of being overwritten;
        ``force=True`` overwrites anyway.
        """
        body: dict[str, Any] = {"files": files, "replace": replace}
        if expected_sha256 is not None:
            body["expected_sha256"] = expected_sha256
        if force:
            body["force"] = True
        return self._run(ops.build_skills_replace_skill_bundle(skill_id, body=body))

    def import_bundle(
        self,
        files: dict[str, str],
        *,
        visibility: str = "private",
        slug_hint: str | None = None,
        source: str | None = None,
    ) -> Any:
        body = clean_body(
            {"files": files, "visibility": visibility, "slug_hint": slug_hint, "source": source}
        )
        return self._run(ops.build_skills_import_skill_bundle(body=body))

    @returns(ops.build_skills_upsert_skill_file)
    def put_file(self, skill_id: str, file_path: str, content: str) -> Any:
        return self._run(
            ops.build_skills_upsert_skill_file(skill_id, file_path, body={"content": content})
        )


def bundle_sha256(files: dict[str, str]) -> str:
    """The digest Beakr reports as a bundle's ``files_sha256``.

    ``sha256`` over ``path\\0sha256(content)`` lines in path order, matching the
    server (``beakr.services.skills.revisions.bundle_files_sha256``), so a client
    can compute the ``expected_sha256`` of a bundle it holds.
    """
    lines = [
        f"{path}\0{hashlib.sha256((files[path] or '').encode('utf-8')).hexdigest()}"
        for path in sorted(files)
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
