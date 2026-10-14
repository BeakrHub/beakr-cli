"""Release preparation derives the package version and notes from the API contract.

Spec W6.3 (audit C060): SDKs regenerate on every public-spec change, follow
semver, and carry a changelog derived from the API changelog. These tests run
``scripts/prepare_release.py`` against a temporary package and engine, and
check this repository's own ``release/config.json``. The script and this test
are identical in every Beakr client repository (beakr-python, beakr-typescript,
beakr-cli); only ``release/config.json`` differs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = _load("prepare_release")

_CONTRACT = {
    "openapi": "3.1.0",
    "info": {"version": "1.0.0", "title": "Beakr"},
    "paths": {
        "/v1/things": {
            "get": {
                "summary": "List things.",
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "string"}},
                                    "required": ["id"],
                                }
                            }
                        }
                    }
                },
            }
        }
    },
    "components": {"schemas": {}},
}

_API_CHANGELOG = """# Public API changelog

<!-- maintainer note: not published -->

## 1.0.0 (unreleased)

### Conventions

- **Pagination.** Every list pages by cursor,
  with `limit` up to 200.
- **Errors.** Every error has a code.

### Projects

- Projects can be archived.
"""


def _copy(contract: dict) -> dict:
    return json.loads(json.dumps(contract))


_VERSION_FILES = [
    ["package.json", '(?s)^\\{\\s*"name": "@beakr/thing",\\s*"version": "([^"]+)"'],
    ["package-lock.json", '(?s)^\\{\\s*"name": "@beakr/thing",\\s*"version": "([^"]+)"'],
    [
        "package-lock.json",
        '"packages": \\{\\s*"": \\{\\s*"name": "@beakr/thing",\\s*"version": "([^"]+)"',
    ],
    ["src/version.py", '__version__ = "([^"]+)"'],
]


def _package(root: Path, version: str = "0.4.0") -> None:
    (root / "src").mkdir(parents=True)
    (root / "release").mkdir()
    (root / "release/config.json").write_text(
        json.dumps(
            {
                "package": "@beakr/thing",
                "changelog_intro": "Releases of `@beakr/thing`.",
                "client_notes_title": "Thing changes",
                "version_files": _VERSION_FILES,
            }
        )
    )
    (root / "package.json").write_text(
        f'{{\n  "name": "@beakr/thing",\n  "version": "{version}"\n}}\n'
    )
    (root / "package-lock.json").write_text(
        f'{{\n  "name": "@beakr/thing",\n  "version": "{version}",\n  "packages": {{\n'
        f'    "": {{\n      "name": "@beakr/thing",\n'
        f'      "version": "{version}"\n    }}\n  }}\n}}\n'
    )
    (root / "src/version.py").write_text(f'__version__ = "{version}"\n')
    (root / "release/released.json").write_text(
        json.dumps({"engine_commit": "abc", "version": "0.4.0"})
    )


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    _package(root)
    engine = tmp_path / "engine"
    (engine / "docs/api").mkdir(parents=True)
    base = tmp_path / "base"
    base.mkdir()
    (base / "contract.json").write_text(json.dumps(_CONTRACT))
    (base / "CHANGELOG.md").write_text(_API_CHANGELOG)

    def plan(contract: dict, changelog: str = _API_CHANGELOG, reachable: dict | None = None):
        (engine / "docs/api/public-openapi.json").write_text(json.dumps(contract))
        internal = engine / "docs/api/internal-openapi.json"
        if reachable is not None:
            internal.write_text(json.dumps(reachable))
        elif internal.exists():
            internal.unlink()
        (engine / "docs/api/CHANGELOG.md").write_text(changelog)
        args = argparse.Namespace(
            engine=str(engine),
            base_contract=str(base / "contract.json"),
            base_changelog=str(base / "CHANGELOG.md"),
        )
        return release.make_plan(args, root)

    return root, plan


# -- version rules ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "level", "expected"),
    [
        ("1.2.3", "major", "2.0.0"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "patch", "1.2.4"),
        # Below 1.0 a breaking change moves the minor version.
        ("0.4.0", "major", "0.5.0"),
        ("0.4.0", "none", "0.4.0"),
    ],
)
def test_semver_bumps(version, level, expected):
    assert str(release.Version.parse(version).bumped(level)) == expected


def test_the_contract_change_decides_the_bump():
    added = _copy(_CONTRACT)
    added["paths"]["/v1/other"] = _copy(_CONTRACT["paths"]["/v1/things"])
    reworded = _copy(_CONTRACT)
    reworded["paths"]["/v1/things"]["get"]["summary"] = "List every thing."
    removed = _copy(_CONTRACT)
    del removed["paths"]["/v1/things"]
    assert release.required_bump(_CONTRACT, _CONTRACT)[0] == "none"
    assert release.required_bump(_CONTRACT, reworded)[0] == "patch"
    assert release.required_bump(_CONTRACT, added)[0] == "minor"
    level, breaks = release.required_bump(_CONTRACT, removed)
    assert level == "major" and breaks == ["GET /v1/things: operation removed"]
    assert release.required_bump(None, _CONTRACT)[0] == "initial"


# -- notes -------------------------------------------------------------------------


def test_changelog_bullets_keep_sections_and_continuations_and_drop_comments():
    sections = release.changelog_bullets(_API_CHANGELOG)
    assert [s.title for s in sections] == ["Conventions", "Projects"]
    assert sections[0].bullets[0].endswith("with `limit` up to 200.")
    assert "maintainer note" not in json.dumps([s.bullets for s in sections])


def test_only_entries_new_since_the_release_are_notes():
    current = _API_CHANGELOG + "- Projects can be renamed.\n"
    fresh = release.new_bullets(_API_CHANGELOG, current)
    assert [(s.title, s.bullets) for s in fresh] == [("Projects", ["- Projects can be renamed."])]


# -- plan, apply, check ------------------------------------------------------------


def test_an_additive_change_requires_a_minor_bump_and_apply_makes_it(setup):
    root, plan = setup
    added = _copy(_CONTRACT)
    added["paths"]["/v1/other"] = _copy(_CONTRACT["paths"]["/v1/things"])
    changelog = _API_CHANGELOG + "- Other things can be listed.\n"
    first = plan(added, changelog)
    assert (first.bump, first.required) == ("minor", "0.5.0")
    assert any("below 0.5.0" in p for p in release.cmd_check(first, root))

    heading = release.cmd_apply(first, "2026-10-10", root)
    assert heading == "@beakr/thing 0.5.0"
    for name, _pattern in _VERSION_FILES:
        assert '"0.5.0"' in (root / name).read_text(), name
    lock = (root / "package-lock.json").read_text()
    assert lock.count('"version": "0.5.0"') == 2
    notes = (root / "CHANGELOG.md").read_text()
    assert notes.startswith("# Changelog\n\nReleases of `@beakr/thing`.\n")
    assert "## @beakr/thing 0.5.0 (2026-10-10)" in notes
    assert "- Other things can be listed." in notes
    assert "Projects can be archived" not in notes  # released before

    assert release.cmd_check(plan(added, changelog), root) == []


def test_new_api_entries_after_apply_fail_the_check_until_reapplied(setup):
    root, plan = setup
    added = _copy(_CONTRACT)
    added["paths"]["/v1/other"] = _copy(_CONTRACT["paths"]["/v1/things"])
    release.cmd_apply(plan(added, _API_CHANGELOG + "- One.\n"), "unreleased", root)
    later = plan(added, _API_CHANGELOG + "- One.\n- Two.\n")
    assert any("- Two." in p for p in release.cmd_check(later, root))
    release.cmd_apply(later, "unreleased", root)
    assert release.cmd_check(plan(added, _API_CHANGELOG + "- One.\n- Two.\n"), root) == []
    assert (root / "CHANGELOG.md").read_text().count("## @beakr/thing 0.5.0") == 1


def test_reapplying_keeps_the_hand_written_client_notes(setup):
    # ``apply`` regenerates a pending release from the API changelog; before
    # it learned about the hand-written client subsection (``client_notes_title``
    # in release/config.json), a re-apply silently dropped notes on client
    # behavior (sync defaults, flags) that no API changelog entry describes.
    root, plan = setup
    added = _copy(_CONTRACT)
    added["paths"]["/v1/other"] = _copy(_CONTRACT["paths"]["/v1/things"])
    heading = release.cmd_apply(plan(added, _API_CHANGELOG + "- One.\n"), "unreleased", root)
    path = root / "CHANGELOG.md"
    text = path.read_text()
    section = "### Thing changes\n\n- `beakr things sync` proposes instead of writing.\n"
    end = text.find("\n## ", text.find(f"## {heading} (") + 3)
    insert_at = len(text) if end == -1 else end + 1
    path.write_text(text[:insert_at].rstrip("\n") + "\n\n" + section + "\n" + text[insert_at:])

    release.cmd_apply(plan(added, _API_CHANGELOG + "- One.\n- Two.\n"), "unreleased", root)

    notes = path.read_text()
    assert "- Two." in notes
    assert notes.count("### Thing changes") == 1
    assert "- `beakr things sync` proposes instead of writing." in notes
    assert release.cmd_check(plan(added, _API_CHANGELOG + "- One.\n- Two.\n"), root) == []


def test_a_breaking_change_below_one_point_zero_requires_a_minor_bump(setup):
    root, plan = setup
    removed = _copy(_CONTRACT)
    del removed["paths"]["/v1/things"]
    result = plan(removed)
    assert result.bump == "major"
    assert result.required == "0.5.0"
    assert result.breaking == ["GET /v1/things: operation removed"]


def test_an_operation_still_served_but_undocumented_is_not_a_breaking_change(setup):
    # Root cause: the documented-core cut (2026-10-12) removed operations from the
    # published contract that the API still serves. prepare_release compared the
    # contracts without the engine gate's still-served rule, so every unlisted
    # operation read as "removed" and forced a major bump.
    root, plan = setup
    removed = _copy(_CONTRACT)
    del removed["paths"]["/v1/things"]
    result = plan(removed, reachable=_CONTRACT)
    assert result.breaking == []
    assert result.bump == "minor"


def test_an_unlisted_operation_with_another_scope_is_still_breaking():
    removed = _copy(_CONTRACT)
    del removed["paths"]["/v1/things"]
    rescoped = _copy(_CONTRACT)
    rescoped["paths"]["/v1/things"]["get"]["x-beakr-scope"] = "kb:write"
    level, breaks = release.required_bump(_CONTRACT, removed, rescoped)
    assert level == "major" and breaks == ["GET /v1/things: operation removed"]
    assert release.required_bump(_CONTRACT, removed, _CONTRACT) == ("minor", [])


def test_versions_out_of_step_are_refused(setup):
    root, plan = setup
    (root / "src/version.py").write_text('__version__ = "0.3.9"\n')
    with pytest.raises(SystemExit, match="out of step"):
        plan(_CONTRACT)


def test_no_change_and_no_new_version_passes(setup):
    root, plan = setup
    assert release.cmd_check(plan(_CONTRACT), root) == []


def test_record_writes_the_released_version(setup):
    root, _plan = setup
    record = release.cmd_record("def456", root)
    assert record == {"engine_commit": "def456", "version": "0.4.0"}
    assert json.loads((root / "release/released.json").read_text()) == record


def test_a_first_release_needs_notes_for_every_api_changelog_entry(setup):
    root, plan = setup
    (root / "release/released.json").write_text(
        json.dumps({"engine_commit": None, "version": None})
    )
    args = argparse.Namespace(
        engine=str(root.parent / "engine"), base_contract=None, base_changelog=None
    )
    plan(_CONTRACT)  # writes the engine's files
    first = release.make_plan(args, root)
    assert (first.bump, first.required) == ("initial", None)
    assert any("no release notes" in p for p in release.cmd_check(first, root))
    release.cmd_apply(first, "unreleased", root)
    assert release.cmd_check(release.make_plan(args, root), root) == []


# -- this repository ---------------------------------------------------------------


def test_an_engine_snapshot_holds_the_last_release_and_what_is_still_served(tmp_path):
    # Root cause: client CI stopped checking the engine out (it reads a committed
    # snapshot, scripts/refresh_snapshots.py). The check read the last release's
    # contract and changelog with `git show` in the engine, and the still-served
    # operations from its full internal contract; a snapshot has neither, so
    # they come from the snapshot's release-base/ and still-served-openapi.json.
    root = tmp_path / "package"
    root.mkdir()
    _package(root)  # the last release was built from engine commit "abc"
    snapshot = tmp_path / "snapshot"
    base = snapshot / "release-base/abc/docs/api"
    base.mkdir(parents=True)
    (base / "public-openapi.json").write_text(json.dumps(_CONTRACT))
    (base / "CHANGELOG.md").write_text(_API_CHANGELOG)
    unlisted = _copy(_CONTRACT)
    del unlisted["paths"]["/v1/things"]
    (snapshot / "docs/api").mkdir(parents=True)
    (snapshot / "docs/api/public-openapi.json").write_text(json.dumps(unlisted))
    (snapshot / "docs/api/CHANGELOG.md").write_text(_API_CHANGELOG)
    args = argparse.Namespace(engine=str(snapshot), base_contract=None, base_changelog=None)

    plan = release.make_plan(args, root)
    assert (plan.bump, plan.breaking) == ("major", ["GET /v1/things: operation removed"])

    (snapshot / "docs/api/still-served-openapi.json").write_text(json.dumps(_CONTRACT))
    plan = release.make_plan(args, root)
    assert (plan.bump, plan.breaking) == ("minor", [])


def test_this_repositorys_version_files_agree():
    config = release.load_config(_REPO)
    found = release.read_versions(_REPO, config.version_files)
    assert len(found) == len(config.version_files), (
        f"release/config.json lists a version file that has no version: {found}"
    )
    assert release._single(found, config.package)


# -- the vendored rules match the engine's ---------------------------------------


def test_vendored_compat_rules_match_the_engine():
    # Honours BEAKR_ENGINE_PATH (both CI layouts) and fails rather than skips
    # under BEAKR_REQUIRE_ENGINE=1, so CI always compares the release rules.
    from tests._engine import engine_checkout

    engine = engine_checkout("beakr/api/public/compat.py")
    vendored = (_REPO / "scripts/contract_compat.py").read_text()
    body = vendored.split("\n", 3)[3]
    assert body == engine.read_text(), "re-vendor beakr/api/public/compat.py"
