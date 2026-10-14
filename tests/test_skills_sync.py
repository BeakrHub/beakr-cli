"""``beakr skills sync``: two-way, with conflicts detected against the last sync."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.hook_runtime import read_state
from beakr_cli.main import internal_app as app

API = "https://api.test"
REMOTE_SKILL_MD = "---\nname: triage\ndescription: Triage bugs\n---\nSteps.\n"
REMOTE_FILES = {"SKILL.md": REMOTE_SKILL_MD, "scripts/run.py": "print(1)\n"}


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _library(updated_at: str = "2026-10-01T00:00:00Z") -> list[dict]:
    return [{"id": "s1", "slug": "triage", "name": "triage", "updated_at": updated_at}]


def _mock_remote(updated_at: str = "2026-10-01T00:00:00Z") -> dict[str, respx.Route]:
    return {
        "library": respx.get(f"{API}/v1/me/skills/library").mock(
            return_value=httpx.Response(200, json=_library(updated_at))
        ),
        "bundle": respx.get(f"{API}/v1/me/skills/s1/bundle").mock(
            return_value=httpx.Response(
                200, json={"skill_id": "s1", "slug": "triage", "files": REMOTE_FILES}
            )
        ),
        "replace": respx.put(f"{API}/v1/me/skills/s1/bundle").mock(
            return_value=httpx.Response(200, json={"skill_id": "s1", "slug": "triage"})
        ),
        "import": respx.post(f"{API}/v1/me/skills/import/bundle").mock(
            return_value=httpx.Response(
                201, json={"id": "s2", "slug": "notes", "updated_at": "2026-10-02T00:00:00Z"}
            )
        ),
        # Shared scopes: none in these library-only cases.
        "scoped": respx.get(f"{API}/v1/skills").mock(
            return_value=httpx.Response(
                200, json={"data": [], "next_cursor": None, "has_more": False}
            )
        ),
    }


def _sync(*args: str):
    return CliRunner().invoke(app, ["skills", "sync", "--json", *args])


def _skill_dir(home: Path, target: str, slug: str) -> Path:
    return home / f".{target}" / "skills" / slug


@respx.mock
def test_pull_writes_both_targets_and_records_state(isolated_home: Path) -> None:
    routes = _mock_remote()

    result = _sync("--direction", "pull")

    assert result.exit_code == 0, result.output
    for target in ("claude", "codex"):
        directory = _skill_dir(isolated_home, target, "triage")
        assert (directory / "SKILL.md").read_text() == REMOTE_SKILL_MD
        assert (directory / "scripts" / "run.py").read_text() == "print(1)\n"
    entry = read_state("skills_sync.json")["skills"]["triage"]
    assert entry["skill_id"] == "s1"
    assert set(entry["local_sha256"]) == {"claude", "codex"}

    again = _sync("--direction", "pull")
    assert {a["action"] for a in json.loads(again.stdout)} == {"unchanged"}
    assert routes["bundle"].call_count == 2  # once per target on the first run only


@respx.mock
def test_pull_reports_conflict_when_both_sides_changed(isolated_home: Path) -> None:
    _mock_remote()
    _sync("--direction", "pull", "--target", "claude")
    skill_md = _skill_dir(isolated_home, "claude", "triage") / "SKILL.md"
    skill_md.write_text(REMOTE_SKILL_MD + "My local edit.\n")
    respx.get(f"{API}/v1/me/skills/library").mock(
        return_value=httpx.Response(200, json=_library("2026-10-05T00:00:00Z"))
    )

    result = _sync("--direction", "pull", "--target", "claude")

    assert result.exit_code == 5
    assert json.loads(result.stdout)[0]["action"] == "conflict"
    assert "My local edit." in skill_md.read_text()

    forced = _sync("--direction", "pull", "--target", "claude", "--force")
    assert forced.exit_code == 0
    assert skill_md.read_text() == REMOTE_SKILL_MD


@respx.mock
def test_local_edit_is_pushed_as_a_bundle_replacement(isolated_home: Path) -> None:
    routes = _mock_remote()
    _sync("--direction", "pull", "--target", "claude")
    skill_md = _skill_dir(isolated_home, "claude", "triage") / "SKILL.md"
    skill_md.write_text(REMOTE_SKILL_MD + "Better steps.\n")

    pulled = _sync("--direction", "pull", "--target", "claude")
    assert json.loads(pulled.stdout)[0]["action"] == "local-changes"

    pushed = _sync("--direction", "push", "--target", "claude")

    assert pushed.exit_code == 0, pushed.output
    assert json.loads(pushed.stdout)[0]["action"] == "pushed"
    body = json.loads(routes["replace"].calls[0].request.content)
    assert body["replace"] is True
    assert body["files"]["SKILL.md"].endswith("Better steps.\n")
    assert body["files"]["scripts/run.py"] == "print(1)\n"


@respx.mock
def test_new_local_skill_is_imported_and_reserved_or_unnamed_dirs_skipped(
    isolated_home: Path,
) -> None:
    routes = _mock_remote()
    root = isolated_home / ".claude" / "skills"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "SKILL.md").write_text("---\nname: notes\ndescription: Notes\n---\nx\n")
    (root / "beakr").mkdir()
    (root / "beakr" / "SKILL.md").write_text("---\nname: beakr\n---\n")
    (root / "draft").mkdir()
    (root / "draft" / "SKILL.md").write_text("no frontmatter\n")

    result = _sync("--direction", "push", "--target", "claude")

    actions = {a["slug"]: a["action"] for a in json.loads(result.stdout)}
    assert actions == {"draft": "skipped", "notes": "pushed-new"}
    body = json.loads(routes["import"].calls[0].request.content)
    assert body["slug_hint"] == "notes"
    assert body["files"] == {"SKILL.md": "---\nname: notes\ndescription: Notes\n---\nx\n"}
    assert read_state("skills_sync.json")["skills"]["notes"]["skill_id"] == "s2"


@respx.mock
def test_push_refuses_to_overwrite_an_untracked_remote_skill(isolated_home: Path) -> None:
    routes = _mock_remote()
    directory = _skill_dir(isolated_home, "claude", "triage")
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: triage\n---\nmine\n")

    result = _sync("--direction", "push", "--target", "claude")

    assert result.exit_code == 5
    assert not routes["replace"].called and not routes["import"].called


@respx.mock
def test_dry_run_changes_nothing(isolated_home: Path) -> None:
    routes = _mock_remote()
    root = isolated_home / ".claude" / "skills" / "notes"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: notes\n---\nx\n")

    result = _sync("--dry-run")

    assert result.exit_code == 0, result.output
    actions = {(a["slug"], a["target"]): a["action"] for a in json.loads(result.stdout)}
    assert actions[("notes", "claude")] == "pushed-new"
    assert actions[("triage", "codex")] == "pulled"
    assert not _skill_dir(isolated_home, "codex", "triage").exists()
    assert not routes["import"].called
    assert read_state("skills_sync.json") == {}


@respx.mock
def test_skills_show_prints_skill_md_by_slug() -> None:
    _mock_remote()
    result = CliRunner().invoke(app, ["skills", "show", "triage"])
    assert result.exit_code == 0
    assert result.stdout == REMOTE_SKILL_MD


def test_sync_rejects_bad_direction() -> None:
    assert _sync("--direction", "sideways").exit_code == 2
