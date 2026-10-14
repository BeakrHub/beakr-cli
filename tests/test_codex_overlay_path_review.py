"""Remote overlay frontmatter must never choose a local path outside skills."""

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app


@pytest.mark.parametrize("absolute", [False, True])
@respx.mock
def test_overlay_target_cannot_escape_the_local_skills_directory(
    isolated_home, monkeypatch, absolute
):
    api = "https://api.test"
    monkeypatch.setenv("BEAKR_API_URL", api)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    outside = isolated_home / ".claude/escaped.overlay"
    target = str(outside.with_suffix("")) if absolute else "../escaped"
    row = {
        "id": "overlay-id",
        "slug": "valid-project-overlay",
        "scope": "project",
        "project_id": "project-a",
        "overlays": target,
        "overlay_ids": [],
        "files_sha256": "hash",
        "can_edit": False,
    }
    respx.get(f"{api}/v1/skills").mock(
        return_value=httpx.Response(
            200, json={"data": [row], "has_more": False, "next_cursor": None}
        )
    )
    respx.get(f"{api}/v1/skills/overlay-id").mock(
        return_value=httpx.Response(
            200,
            json={
                **row,
                "files": {
                    "SKILL.md": (
                        "---\nname: project-overlay\ndescription: Project rules\n"
                        f"overlays: {target}\n---\nRemote content.\n"
                    )
                },
            },
        )
    )
    result = CliRunner().invoke(
        app,
        [
            "skills",
            "sync",
            "--json",
            "--target",
            "claude",
            "--scope",
            "project",
            "--project",
            "project-a",
            "--direction",
            "pull",
        ],
    )
    # Checking the filesystem, not the error wording, proves containment.
    assert not outside.exists(), f"Remote overlay wrote outside skills: {result.output}"
