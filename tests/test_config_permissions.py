"""The Beakr home and ``config.json`` are owner-only, and an old stored key does not stay readable.

Regression: up to 0.3.2 ``config.save`` created ``~/.beakr`` and wrote
``config.json``, which then held the API key, with the process umask (typically
0755 and 0644), so any local user could read the key. 0.3.3 wrote them
0700/0600 and tightened what an older release had created. From 0.4.0 the key
is not in ``config.json`` at all (it is in the token store); these tests keep
the 0.3.3 guarantees: settings are written owner-only, and a key an older
release left in a world-readable ``config.json`` is moved out and the file
rewritten 0600 the first time the CLI reads it.
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from typer.testing import CliRunner

from beakr_cli import config

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")

KEY = "bk_live_permissions_test_key_value"


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_saving_settings_creates_an_owner_only_directory_and_file():
    previous_umask = os.umask(0o022)
    try:
        config.set_key("api_url", "https://api.example.test")
    finally:
        os.umask(previous_umask)

    assert _mode(config.config_dir()) == 0o700
    assert _mode(config.config_file()) == 0o600
    # The atomic write leaves no temporary file behind.
    assert [p.name for p in config.config_dir().iterdir()] == ["config.json"]


def test_reading_moves_a_key_an_older_release_left_world_readable():
    home = config.config_dir()
    home.mkdir(mode=0o755, parents=True)
    home.chmod(0o755)
    config.config_file().write_text(json.dumps({"api_key": KEY}) + "\n")
    config.config_file().chmod(0o644)

    config.load()

    assert "api_key" not in json.loads(config.config_file().read_text())
    assert _mode(home) == 0o700
    assert _mode(config.config_file()) == 0o600


def test_saving_tightens_an_existing_world_readable_file():
    home = config.config_dir()
    home.mkdir(mode=0o755, parents=True)
    home.chmod(0o755)
    config.config_file().write_text("{}\n")
    config.config_file().chmod(0o644)

    config.save({"api_url": "https://api.example.test"})

    assert _mode(home) == 0o700
    assert _mode(config.config_file()) == 0o600


def test_update_check_cache_creates_the_directory_owner_only():
    from datetime import datetime, timezone

    from beakr_cli import updates

    updates._write_cache("9.9.9", datetime.now(timezone.utc))

    assert _mode(config.config_dir()) == 0o700


def test_failed_login_does_not_echo_or_keep_the_api_key(monkeypatch):
    from beakr_cli.commands import auth

    def reject():
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(auth, "_me", reject)
    result = CliRunner().invoke(auth.app, ["login", "--api-key", KEY])

    assert result.exit_code == 3
    assert KEY not in result.output
    assert config.get("api_key") is None
    assert KEY not in "".join(p.read_text() for p in config.config_dir().rglob("*") if p.is_file())
