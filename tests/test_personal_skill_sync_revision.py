"""Personal skill sync writes only over the bundle it last synced (review R027).

Root cause: a personal push called ``replace_bundle`` with no base revision, so
an edit made in Beakr after the sync read its library was overwritten. A push
now sends the server digest of the last-synced bundle as ``expected_sha256``;
a 409 ``skill_conflict`` is reported and the local edit is kept.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from beakr_cli._private_api.resources.skills import bundle_sha256
from beakr_cli.main import internal_app as app
from tests.test_skills_sync import REMOTE_FILES, REMOTE_SKILL_MD, _mock_remote, _skill_dir

API = "https://api.test"


def _sync(*args: str):
    return CliRunner().invoke(app, ["skills", "sync", "--json", *args])


def test_bundle_sha256_matches_the_server_digest():
    # Computed with beakr.services.skills.revisions.bundle_files_sha256.
    files = {
        "SKILL.md": "---\nname: X\n---\nBody\n",
        "references/a.md": "é unicode",
        "empty.txt": "",
    }
    assert bundle_sha256(files) == (
        "2b62c3ac9102f4f41948ad05e3215d0452cb77e64dcc715445d03f31e2ebe271"
    )


@respx.mock
def test_a_push_sends_the_last_synced_bundle_as_its_base(isolated_home: Path, monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    routes = _mock_remote()
    _sync("--direction", "pull", "--target", "claude")
    skill_md = _skill_dir(isolated_home, "claude", "triage") / "SKILL.md"
    skill_md.write_text(REMOTE_SKILL_MD + "Better steps.\n")

    pushed = _sync("--direction", "push", "--target", "claude")

    assert pushed.exit_code == 0, pushed.output
    body = json.loads(routes["replace"].calls[0].request.content)
    assert body["expected_sha256"] == bundle_sha256(REMOTE_FILES)
    assert "force" not in body


@respx.mock
def test_a_conflict_is_reported_and_the_local_edit_is_kept(isolated_home: Path, monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    routes = _mock_remote()
    _sync("--direction", "pull", "--target", "claude")
    skill_md = _skill_dir(isolated_home, "claude", "triage") / "SKILL.md"
    skill_md.write_text(REMOTE_SKILL_MD + "Mine.\n")
    routes["replace"].mock(
        return_value=httpx.Response(
            409,
            json={"error": {"code": "skill_conflict", "message": "The skill changed."}},
        )
    )

    pushed = _sync("--direction", "push", "--target", "claude")

    actions = json.loads(pushed.stdout)
    assert actions[0]["action"] == "conflict"
    assert skill_md.read_text().endswith("Mine.\n")

    # The base is unchanged, so the next push still conflicts rather than
    # silently adopting the local copy as synced.
    again = _sync("--direction", "push", "--target", "claude")
    assert json.loads(again.stdout)[0]["action"] == "conflict"


@respx.mock
def test_force_overwrites_and_says_so(isolated_home: Path, monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    routes = _mock_remote()
    _sync("--direction", "pull", "--target", "claude")
    skill_md = _skill_dir(isolated_home, "claude", "triage") / "SKILL.md"
    skill_md.write_text(REMOTE_SKILL_MD + "Mine.\n")

    _sync("--direction", "push", "--target", "claude", "--force")

    body = json.loads(routes["replace"].calls[0].request.content)
    assert body["force"] is True
