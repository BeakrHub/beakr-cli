"""Skill sync never reads, uploads or writes through a link (audit C078).

Root cause: ``read_local_files`` followed file symlinks inside a skill folder,
so a ``references/x`` link to a file outside the skill sent that file's bytes to
Beakr on push (and the same reader fed shared replacements and proposals), and
a pull could write through a link to a file outside the folder.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_cli._private_api import PrivateBeakr as Beakr
from beakr_cli.skills_sync import (
    UnsafeSkillFolder,
    linked_entries,
    read_local_files,
    sync_skills,
    target_root,
    write_local_files,
)

API = "https://api.test"
SECRET = "SYNTHETIC-OUTSIDE-BYTES"


def _skill(root, name="review-skill"):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: Review skill\n---\nSteps.\n")
    return directory


def _push(mock):
    mock.get(f"{API}/v1/me/skills/library").respond(200, json=[])
    uploaded = mock.post(f"{API}/v1/me/skills/import/bundle").respond(
        201, json={"id": "s1", "slug": "review-skill"}
    )
    actions = sync_skills(
        Beakr(api_key="bk_live_x", base_url=API), direction="push", target="claude"
    )
    return uploaded, actions


def test_an_inner_file_link_refuses_the_whole_skill(isolated_home, tmp_path):
    directory = _skill(target_root("claude"))
    (tmp_path / "outside.txt").write_text(SECRET)
    (directory / "references").mkdir()
    (directory / "references" / "x.txt").symlink_to(tmp_path / "outside.txt")
    with respx.mock as mock:
        uploaded, actions = _push(mock)
    assert uploaded.call_count == 0
    assert [(a.slug, a.action) for a in actions] == [("review-skill", "refused")]


def test_an_inner_directory_link_is_refused_and_never_walked(isolated_home, tmp_path):
    directory = _skill(target_root("claude"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text(SECRET)
    (directory / "references").symlink_to(outside, target_is_directory=True)
    assert linked_entries(directory) == ["references"]
    assert "references/notes.md" not in read_local_files(directory)
    with respx.mock as mock:
        uploaded, _ = _push(mock)
    assert uploaded.call_count == 0


def test_a_linked_skill_md_is_refused(isolated_home, tmp_path):
    root = target_root("claude")
    directory = root / "review-skill"
    directory.mkdir(parents=True)
    real = tmp_path / "SKILL.md"
    real.write_text("---\nname: Review skill\n---\n" + SECRET)
    (directory / "SKILL.md").symlink_to(real)
    with respx.mock as mock:
        uploaded, _ = _push(mock)
    assert all(SECRET not in call.request.content.decode() for call in uploaded.calls)
    assert uploaded.call_count == 0


def test_an_ordinary_nested_reference_still_uploads(isolated_home):
    directory = _skill(target_root("claude"))
    (directory / "references").mkdir()
    (directory / "references" / "guide.md").write_text("Nested guide")
    with respx.mock as mock:
        uploaded, _ = _push(mock)
    assert uploaded.call_count == 1
    body = json.loads(uploaded.calls[0].request.content)
    assert "Nested guide" in json.dumps(body)


def test_a_pull_never_writes_through_a_link(tmp_path):
    directory = tmp_path / "skill"
    directory.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    (directory / "notes.md").symlink_to(victim)
    with pytest.raises(UnsafeSkillFolder):
        write_local_files(directory, {"SKILL.md": "x", "notes.md": "overwritten"})
    assert victim.read_text() == "keep me"
    assert not (directory / "SKILL.md").exists()


