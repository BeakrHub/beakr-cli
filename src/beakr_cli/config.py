"""Non-secret CLI settings in ``config.json`` under the Beakr home.

The home is the SDK's :func:`beakr_sdk.credentials.beakr_home` (``~/.beakr``,
or ``BEAKR_HOME``), read at call time, so the CLI and the SDK always read the
same place. The directory is kept 0700 and the file is replaced atomically at
0600 (:func:`beakr_sdk.credentials.write_private_file`).

Secrets never live here. An API key is saved in the token store beside
sign-ins (the keychain, else ``credentials`` at 0600). CLI 0.3.x saved it here
in plain text at 0644; :func:`migrate_secrets` moves such a key into the store
the next time the CLI reads its settings.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from beakr_sdk.credentials import beakr_home, write_private_file

#: Keys that must never be written to ``config.json``.
SECRET_KEYS = frozenset({"api_key"})


def config_dir() -> Path:
    return beakr_home()


def config_file() -> Path:
    return beakr_home() / "config.json"


def ensure_dir() -> Path:
    """The Beakr home, created (or tightened) to 0700."""
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(directory, stat.S_IRWXU)
    return directory


def _read() -> dict[str, Any]:
    path = config_file()
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def load() -> dict[str, Any]:
    """The settings, after moving any secret an older CLI left in them."""
    data = _read()
    if SECRET_KEYS & data.keys():
        migrate_secrets()
        data = _read()
    return data


def save(data: dict[str, Any]) -> None:
    leaked = SECRET_KEYS & data.keys()
    if leaked:
        raise ValueError(f"{sorted(leaked)} are secrets: save them with save_api_key, not here")
    ensure_dir()
    write_private_file(config_file(), json.dumps(data, indent=2) + "\n")


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set_key(key: str, value: Any) -> None:
    data = load()
    data[key] = value
    save(data)


def delete_key(key: str) -> None:
    data = load()
    if key in data:
        data.pop(key)
        save(data)


def save_api_key(api_key: str, base_url: str) -> None:
    """Save an API key for ``base_url`` in the token store (keychain, else a 0600 file).

    It takes the place of any sign-in for the same API, which would otherwise
    outrank it.
    """
    from beakr_sdk.credentials import StoredTokens, TokenStore

    ensure_dir()
    TokenStore().save(StoredTokens.for_api_key(api_key, base_url=base_url))


def migrate_secrets() -> bool:
    """Move an ``api_key`` an older CLI saved in ``config.json`` into the token store.

    The key is filed under the API it was saved for (``api_url``, else
    production), removed from the file, and the file is rewritten at 0600.
    Returns whether anything moved. Safe to call on every run.
    """
    from beakr_sdk.credentials import DEFAULT_BASE_URL, TokenStore

    data = _read()
    api_key = data.pop("api_key", None)
    if not api_key:
        return False
    base_url = str(data.get("api_url") or DEFAULT_BASE_URL)
    existing = TokenStore().load(base_url)
    # A newer sign-in for the same API already outranks the old key; keep it.
    if existing is None:
        save_api_key(str(api_key), base_url)
    save(data)
    return True
