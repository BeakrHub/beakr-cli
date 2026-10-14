"""Persistent configuration stored in ~/.beakr/config.json.

The file holds the API key, so it is readable by its owner only: the directory
is 0700 and the file 0600. Releases up to 0.3.2 wrote both with the default
umask (usually 0755/0644, readable by every local user); ``load`` and ``save``
tighten an existing directory and file the first time this version touches them.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".beakr"
CONFIG_FILE = CONFIG_DIR / "config.json"

_DIR_MODE = 0o700
_FILE_MODE = 0o600
# POSIX permission bits mean nothing useful on Windows, where the profile
# directory is already private to the user.
_ENFORCE_MODES = os.name != "nt"


def _tighten(path: Path, mode: int) -> None:
    """Remove group and other access from ``path`` if it has any."""
    if not _ENFORCE_MODES:
        return
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        if current & 0o077:
            path.chmod(mode)
    except OSError:
        # Best effort: a file owned by someone else, or on a filesystem without
        # POSIX modes, must not stop the command from running.
        return


def ensure_dir() -> None:
    """Create ~/.beakr owner-only, or tighten it if an older release made it."""
    CONFIG_DIR.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    _tighten(CONFIG_DIR, _DIR_MODE)


def load() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    _tighten(CONFIG_DIR, _DIR_MODE)
    _tighten(CONFIG_FILE, _FILE_MODE)
    return json.loads(CONFIG_FILE.read_text())


def save(data: dict[str, Any]) -> None:
    ensure_dir()
    # mkstemp creates the file 0600; renaming it over the config means the key
    # is never on disk with wider permissions, even for an instant.
    fd, tmp_name = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp_name, CONFIG_FILE)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set_key(key: str, value: Any) -> None:
    data = load()
    data[key] = value
    save(data)


def delete_key(key: str) -> None:
    data = load()
    data.pop(key, None)
    save(data)
