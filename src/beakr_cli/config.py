"""Persistent configuration stored in ~/.beakr/config.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".beakr"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text())


def save(data: dict[str, Any]) -> None:
    _ensure_dir()
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")


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
