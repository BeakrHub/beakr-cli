"""Engine-comparing tests find the engine in CI layouts and fail rather than skip there.

Root cause (Codex C060 follow-up): the compat-rules parity test looked only for a
sibling checkout, ignored BEAKR_ENGINE_PATH, and skipped when absent, so in both
CI layouts (engine at $GITHUB_WORKSPACE/engine, or the engine as the workspace)
the release gate silently stopped comparing.
"""

from __future__ import annotations

import pytest
from _pytest.outcomes import Failed, Skipped

from tests import _engine as engine_lookup
from tests._engine import engine_checkout


def test_the_configured_engine_path_wins(tmp_path, monkeypatch):
    target = tmp_path / "engine" / "beakr/api/public/compat.py"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    monkeypatch.setenv("BEAKR_ENGINE_PATH", str(tmp_path / "engine"))
    assert engine_checkout("beakr/api/public/compat.py") == target


def test_a_missing_engine_skips_locally_and_fails_when_required(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAKR_ENGINE_PATH", str(tmp_path / "nowhere"))
    relative = "no/such/file-anywhere.py"
    monkeypatch.delenv("BEAKR_REQUIRE_ENGINE", raising=False)
    with pytest.raises(Skipped):
        engine_checkout(relative)
    monkeypatch.setenv("BEAKR_REQUIRE_ENGINE", "1")
    with pytest.raises(Failed):
        engine_checkout(relative)


def test_without_a_checkout_the_committed_snapshot_is_read(tmp_path, monkeypatch):
    # Client CI has no engine checkout: contract tests read the snapshot that
    # scripts/refresh_snapshots.py commits, never skip.
    monkeypatch.delenv("BEAKR_ENGINE_PATH", raising=False)
    monkeypatch.setattr(engine_lookup, "_REPO", tmp_path / "repo")
    found = engine_checkout("docs/api/public-openapi.json")
    assert found == engine_lookup.SNAPSHOT / "docs/api/public-openapi.json"


def test_real_engine_tests_skip_on_the_snapshot_even_when_required(tmp_path, monkeypatch):
    # Root cause: under BEAKR_REQUIRE_ENGINE=1 a test that runs the engine's own
    # code would fail in client CI, which has only the snapshot. It skips there
    # and runs in the engine's CI.
    monkeypatch.setattr(engine_lookup, "_REPO", tmp_path / "repo")
    monkeypatch.setenv("BEAKR_ENGINE_PATH", str(engine_lookup.SNAPSHOT))
    monkeypatch.setenv("BEAKR_REQUIRE_ENGINE", "1")
    with pytest.raises(Skipped):
        engine_lookup.real_engine("beakr/api/public/compat.py")


def test_a_named_engine_without_its_venv_fails_real_engine_tests_when_required(
    tmp_path, monkeypatch
):
    engine = tmp_path / "engine"
    (engine / "beakr/api/public").mkdir(parents=True)
    (engine / "beakr/api/public/compat.py").write_text("x")
    monkeypatch.setattr(engine_lookup, "_REPO", tmp_path / "repo")
    monkeypatch.setenv("BEAKR_ENGINE_PATH", str(engine))
    monkeypatch.setenv("BEAKR_REQUIRE_ENGINE", "1")
    with pytest.raises(Failed):
        engine_lookup.real_engine("beakr/api/public/compat.py")
    python = engine / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert engine_lookup.real_engine("beakr/api/public/compat.py") == engine
