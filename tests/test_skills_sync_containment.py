"""Skill folders stay inside the target's skills directory (audit C047).

Root cause: a project overlay's local folder was built from the server's
``overlays`` frontmatter (``<value>.overlay``) and joined to the skills root
unchecked, so ``../x`` or an absolute value wrote outside it, and the inner
file-path check only guarded paths relative to that already-escaped folder.
These cover the remaining routes out: a symlinked folder on pull and on push,
and a non-canonical slug, none of which may read or write outside the root.
"""

from __future__ import annotations

import pytest

from beakr_cli.skills_sync import UnsafeSkillFolder, skill_dir, target_root


@pytest.mark.parametrize("name", ["..", ".", "a/b", "/etc", "", "a\\b"])
def test_a_folder_name_that_is_not_one_plain_component_is_refused(isolated_home, name):
    with pytest.raises(UnsafeSkillFolder):
        skill_dir("claude", name)


def test_a_symlinked_skill_folder_pointing_outside_is_refused(isolated_home, tmp_path):
    root = target_root("claude")
    root.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / "handbook").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeSkillFolder):
        skill_dir("claude", "handbook")


def test_a_plain_skill_folder_is_allowed(isolated_home):
    assert skill_dir("claude", "handbook") == target_root("claude") / "handbook"


def test_push_does_not_upload_a_symlinked_folder(isolated_home, tmp_path, monkeypatch):
    from beakr_cli import skills_sync

    root = target_root("claude")
    root.mkdir(parents=True)
    outside = tmp_path / "secrets"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: stolen\n---\nprivate\n")
    (root / "stolen").symlink_to(outside, target_is_directory=True)

    pushed: list[str] = []
    runner = skills_sync.SkillsSync.__new__(skills_sync.SkillsSync)
    recorded: list = []
    monkeypatch.setattr(runner, "shared", lambda: {}, raising=False)
    monkeypatch.setattr(runner, "_record", recorded.append, raising=False)
    monkeypatch.setattr(
        runner, "_push_one", lambda directory, *_: pushed.append(directory.name), raising=False
    )
    runner.push(["claude"])
    assert pushed == []
    assert [(a.slug, a.action) for a in recorded] == [("stolen", "refused")]
