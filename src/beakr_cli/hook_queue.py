"""A small, bounded on-disk queue for conversation imports that failed in a hook.

Queued payloads hold private transcript text, so the directory is 0700 and
every file 0600, written atomically. Each payload records the sign-in it was
queued under; only that sign-in ever sends it (see ``compounding.flush_queue``).

A hook cannot wait for the network to come back and must never fail the
agent's session, so an import that fails (offline, API down) is written here
and sent by the next hook run, before anything newer, which keeps a session's
messages in order. The queue holds at most :data:`MAX_QUEUED` files. When it
is full nothing more is queued and the session cursor is not advanced, so the
transcript itself remains the source of truth and the messages are picked up
again once the queue drains -- the bound costs a delay, never data.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beakr_sdk.credentials import beakr_home, write_private_file

MAX_QUEUED = 50
#: A queued import older than this means hooks have been failing for a while.
STALE_AFTER_SECONDS = 24 * 3600


def queue_dir() -> Path:
    return beakr_home() / "hook_queue"


def queued_files() -> list[Path]:
    """Queued payloads, oldest first (file names start with a sortable timestamp)."""
    try:
        return sorted(path for path in queue_dir().iterdir() if path.suffix == ".json")
    except FileNotFoundError:
        return []


def can_enqueue(count: int = 1) -> bool:
    """Whether ``count`` more payloads fit; a run queues all of its batches or none."""
    return len(queued_files()) + count <= MAX_QUEUED


def enqueue(payload: dict[str, Any]) -> Path | None:
    """Queue one import body; None when the queue is full."""
    if not can_enqueue():
        return None
    directory = queue_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(directory, stat.S_IRWXU)
    final = directory / f"{time.time_ns():020d}-{os.getpid()}.json"
    # Atomic and owner-only: a concurrent flush never reads half a payload.
    write_private_file(final, json.dumps(payload))
    return final


def drop_for_key(key: str) -> int:
    """Remove queued payloads for one session; they are superseded by a full resend."""
    dropped = 0
    for path in queued_files():
        payload = read_payload(path)
        if payload is not None and payload.get("key") == key:
            path.unlink(missing_ok=True)
            dropped += 1
    return dropped


def read_payload(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class QueueHealth:
    queued: int
    oldest_age_seconds: float | None
    full: bool

    @property
    def healthy(self) -> bool:
        stale = self.oldest_age_seconds is not None and (
            self.oldest_age_seconds > STALE_AFTER_SECONDS
        )
        return not self.full and not stale

    def as_dict(self) -> dict[str, Any]:
        return {
            "queued": self.queued,
            "max": MAX_QUEUED,
            "oldest_age_seconds": self.oldest_age_seconds,
            "full": self.full,
            "healthy": self.healthy,
            "path": str(queue_dir()),
        }


def queue_health(now: float | None = None) -> QueueHealth:
    files = queued_files()
    current = time.time() if now is None else now
    oldest = None
    if files:
        try:
            oldest = max(0.0, current - files[0].stat().st_mtime)
        except OSError:
            oldest = None
    return QueueHealth(queued=len(files), oldest_age_seconds=oldest, full=len(files) >= MAX_QUEUED)
