"""Skill sync must not upload files outside the selected skill directory."""

import json

import pytest
import respx

from beakr_cli._private_api import PrivateBeakr as Beakr
from beakr_cli.skills_sync import sync_skills, target_root


@pytest.mark.parametrize("symlink", [False, True])
def test_skill_push_keeps_external_file_bytes_out_of_uploads(isolated_home, tmp_path, symlink):
    directory = target_root("claude") / "review-skill"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: Review skill\n---\nReview steps.\n")
    references = directory / "references"
    references.mkdir()
    marker = "SYNTHETIC-OUTSIDE-SKILL-CONTENT"
    if symlink:
        external = tmp_path / "outside.txt"
        external.write_text(marker)
        (references / "reference.txt").symlink_to(external)
    else:
        (references / "reference.txt").write_text("Ordinary bundled reference")

    with respx.mock as mock:
        mock.get("https://api.test/v1/me/skills/library").respond(200, json=[])
        uploaded = mock.post("https://api.test/v1/me/skills/import/bundle").respond(
            201, json={"id": "review-id", "slug": "review-skill"}
        )
        sync_skills(
            Beakr(api_key="bk_live_synthetic_review", base_url="https://api.test"),
            direction="push",
            target="claude",
        )
        if symlink:
            assert all(marker not in call.request.content.decode() for call in uploaded.calls), (
                "Skill sync uploaded external file bytes through an inner symlink"
            )
        else:
            assert uploaded.call_count == 1
            assert "Ordinary bundled reference" in json.dumps(
                json.loads(uploaded.calls[0].request.content)
            )
