"""The committed engine snapshot is what the manifest says, and holds nothing private.

CI reads ``snapshots/beakr-engine`` instead of checking the private engine out
(``scripts/refresh_snapshots.py``). These tests keep it honest: every file is
the one the manifest pins, the engine's full internal contract is never in
it (this repository is meant to be public), and ``verify`` notices a hand edit.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


snapshots = _load("refresh_snapshots")
generator = _load("generate_private_api")


def _operations(document: dict) -> set[tuple[str, str]]:
    return generator.operations_of(document)


def test_the_committed_snapshot_matches_its_manifest():
    assert snapshots.verify(release_commit=snapshots.released_engine_commit()) == []


def test_the_snapshot_never_holds_the_internal_contract():
    engine = snapshots.SNAPSHOTS / "beakr-engine"
    assert not (engine / "docs/api/internal-openapi.json").exists()
    private = json.loads((engine / snapshots.CLI_PRIVATE).read_text())
    allowlisted = set(generator.read_allowlist(generator.ALLOWLIST.read_text()))
    assert _operations(private) == allowlisted


def test_verify_notices_an_edited_or_extra_file(tmp_path):
    copy = tmp_path / "snapshots"
    shutil.copytree(snapshots.SNAPSHOTS, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert snapshots.verify(snapshots=copy) == []
    contract = copy / "beakr-engine" / snapshots.PUBLIC
    contract.write_text(contract.read_text() + " ")
    (copy / "beakr-engine/docs/api/internal-openapi.json").write_text("{}")
    assert snapshots.verify(snapshots=copy) == [
        f"beakr-engine/{snapshots.INTERNAL} does not match the manifest",
        f"beakr-engine/{snapshots.PUBLIC} does not match the manifest",
    ]


def test_a_missing_release_base_is_reported():
    problems = snapshots.verify(release_commit="0" * 40)
    assert problems == [f"beakr-engine has no release base for commit {'0' * 40}"]


def test_refresh_pins_the_commit_and_flags_uncommitted_files(tmp_path):
    repo = tmp_path / "engine"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (repo / "contract.json").write_text("{}\n")
    git("add", "contract.json")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c")
    source = snapshots.Source(
        name="engine",
        repository="BeakrHub/example",
        flag="example",
        env="UNUSED",
        siblings=(),
        copied=("contract.json",),
    )
    out = tmp_path / "snapshots"

    entry = snapshots.write_snapshot(
        source, repo, snapshots.expected_files(source, repo), snapshots=out
    )
    assert len(entry["commit"]) == 40
    assert entry["uncommitted_changes"] is False
    assert entry["files"] == {"contract.json": snapshots.digest(b"{}\n")}

    (repo / "contract.json").write_text('{"changed": true}\n')
    entry = snapshots.write_snapshot(
        source, repo, snapshots.expected_files(source, repo), snapshots=out
    )
    assert entry["uncommitted_changes"] is True
    assert (out / "engine/contract.json").read_text() == '{"changed": true}\n'
