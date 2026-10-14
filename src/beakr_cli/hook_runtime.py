"""What agent hooks need at runtime: a log, small state files, and stdin.

A hook runs inside someone else's agent. It must never print a traceback into
that session or fail the session, so hook-mode commands log here and exit 0.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from beakr_sdk.credentials import beakr_home, write_private_file

#: Keep the log from growing without bound on a machine that hooks every turn.
_LOG_MAX_BYTES = 1_000_000


def hooks_log_path() -> Path:
    return beakr_home() / "hooks.log"


def log_hook(message: str) -> None:
    """Append one timestamped line; never raises."""
    try:
        path = hooks_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _LOG_MAX_BYTES:
            path.replace(path.with_suffix(".log.1"))
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} [{os.getpid()}] {message}\n")
    except OSError:
        pass


def read_state(name: str) -> dict[str, Any]:
    """A JSON state file under ``~/.beakr``; empty when missing or corrupt."""
    try:
        data = json.loads((beakr_home() / name).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(name: str, data: dict[str, Any]) -> None:
    # Atomic (a concurrent hook never reads half a file) and owner-only: state
    # records which sessions were sent, and under which sign-in.
    write_private_file(beakr_home() / name, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_hook_stdin() -> dict[str, Any]:
    """The JSON object a Claude Code hook receives on stdin, or ``{}``."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
