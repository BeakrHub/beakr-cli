"""Keep every test away from the developer's real credentials and agent config.

The CLI and SDK read ~/.beakr, the OS keychain, ~/.claude and ~/.codex. A test
that forgot to isolate one of them would read a real login (and could write
over it), so isolation is applied to every test rather than opted into.
"""

from __future__ import annotations

import os

import pytest

# Typer and Rich decide when they are imported whether to force colored, boxed
# output: they do on a CI runner (GITHUB_ACTIONS) or when FORCE_COLOR/PY_COLORS is
# set. Tests compare plain text, so a run on GitHub Actions failed on ANSI codes
# that a local run never sees. Cleared here, before any test imports the CLI.
for _name in ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS", "TTY_COMPATIBLE"):
    os.environ.pop(_name, None)

_CREDENTIAL_ENV = (
    "BEAKR_API_KEY",
    "BEAKR_API_URL",
    "BEAKR_BASE_URL",
    "BEAKR_ORG_ID",
    "BEAKR_PROJECT_ID",
    "BEAKR_AGENT",
    "BEAKR_DEV_IDENTITY",
    "BEAKR_DEV_EMAIL",
    "BEAKR_DEV_IDENTITY_NAME",
    "BEAKR_DEV_DISPLAY_NAME",
    "BEAKR_SERVICE_ACCOUNT_FILE",
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    beakr_home = home / ".beakr"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BEAKR_HOME", str(beakr_home))
    monkeypatch.setenv("BEAKR_NO_KEYRING", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    for name in _CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    # Tests may run from inside a Claude Code hook; that marker would make
    # every hook command under test stand down.
    monkeypatch.delenv("BEAKR_HOOK_ACTIVE", raising=False)
    # A bound project in the checkout running the tests must not leak in.
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return home


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_project_refs: resolve --project slugs and names through the API"
    )


@pytest.fixture(autouse=True)
def project_refs_are_ids(request, monkeypatch):
    """Treat every ``--project`` value as an id unless a test asks for real resolution.

    Commands resolve a project slug or name to its id with one projects request
    (beakr_cli/project_ref.py). Tests across the suite pass placeholder ids such
    as ``p1``, which would otherwise be looked up; tests of resolution itself
    are marked ``@pytest.mark.real_project_refs``.
    """
    if request.node.get_closest_marker("real_project_refs"):
        return
    from beakr_cli import project_ref

    monkeypatch.setattr(project_ref, "looks_like_id", lambda value: True)


@pytest.fixture
def capture_consented(monkeypatch):
    """Treat every session's repository as having opted in to automatic capture.

    For tests about what capture sends (cursors, queues, orgs), whose
    transcripts name a placeholder cwd with no binding. The consent rule
    itself is tested against real bindings in tests/test_hook_capture_consent.py
    and tests/test_queued_capture_binding.py.
    """
    from beakr_cli import compounding
    from beakr_cli.commands import memory

    monkeypatch.setattr(compounding, "capture_enabled", lambda cwd: True)
    monkeypatch.setattr(memory, "capture_enabled", lambda cwd: True)
    # Their queued payloads are authorized as captured; the rule binding a
    # queued payload to its project, API and org is tested against real
    # bindings in tests/test_queued_capture_binding.py.
    monkeypatch.setattr(compounding, "_queued_capture_problem", lambda queued: None)


def pytest_addoption(parser):
    # tests/test_command_snapshots.py: rewrite golden files instead of comparing
    # (BEAKR_UPDATE_SNAPSHOTS=1 does the same).
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="Rewrite tests/snapshots/*.txt from the current command output.",
    )
