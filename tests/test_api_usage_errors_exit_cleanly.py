"""``beakr api`` usage errors end with a usage message and exit 2, never a traceback.

Regression: a fresh ``uv tool install`` resolved typer 0.27 (pyproject allowed
``typer>=0.15`` and uv.lock is not used for tool installs). typer 0.26+ vendors
its own click, so the real click exceptions ``beakr api``'s generated commands
raise went past typer's handler: ``beakr api --help`` printed its help and then
a traceback ending ``Exit: 0``, and an unknown subcommand or a missing body
field printed a ``UsageError`` traceback with exit 1 (found on a fresh install
against sandbox, 2026-10-25). The fix is the typer cap pinned in
``tests/test_packaging.py``; this runs the installed console script as a real
process -- the path a user takes, which ``CliRunner`` short-cuts -- so a typer
bump in uv.lock that brings the break back fails here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(sys.executable).parent / ("beakr.exe" if os.name == "nt" else "beakr")


def _run(isolated_home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if not SCRIPT.exists():
        pytest.skip(f"no installed console script at {SCRIPT}")
    env = {
        **os.environ,
        "BEAKR_NO_UPDATE_CHECK": "1",
        "BEAKR_API_URL": "https://api.test",
        "BEAKR_API_KEY": "bk_live_test",
        "COLUMNS": "120",
    }
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)


def test_api_help_exits_zero_without_a_traceback(isolated_home: Path) -> None:
    result = _run(isolated_home, "api", "--help")
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "Usage" in result.stdout


def test_unknown_api_command_is_a_usage_error(isolated_home: Path) -> None:
    result = _run(isolated_home, "api", "projects", "bogus")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "No such command" in result.stdout + result.stderr


def test_missing_body_field_is_a_usage_error(isolated_home: Path) -> None:
    result = _run(isolated_home, "api", "projects", "create-project", "--data", "{}")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "--name" in result.stdout + result.stderr
