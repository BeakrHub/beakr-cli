"""~/.beakr and its config.json hold the API key, so they are owner-only.

Regression: up to 0.3.2 ``config.save`` created ``~/.beakr`` and wrote
``config.json`` with the process umask (typically 0755 and 0644), so any local
user could read the stored API key. 0.3.3 writes them 0700/0600 and tightens
directories and files an older release already created.
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


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".beakr")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / ".beakr" / "config.json")
    return tmp_path / ".beakr"


def test_saving_the_key_creates_an_owner_only_directory_and_file(config_home):
    previous_umask = os.umask(0o022)
    try:
        config.set_key("api_key", KEY)
    finally:
        os.umask(previous_umask)

    assert _mode(config_home) == 0o700
    assert _mode(config.CONFIG_FILE) == 0o600
    assert json.loads(config.CONFIG_FILE.read_text())["api_key"] == KEY
    # The atomic write leaves no temporary file behind.
    assert [p.name for p in config_home.iterdir()] == ["config.json"]


def test_reading_tightens_a_directory_and_file_an_older_release_wrote(config_home):
    config_home.mkdir(mode=0o755)
    config_home.chmod(0o755)
    config.CONFIG_FILE.write_text(json.dumps({"api_key": KEY}) + "\n")
    config.CONFIG_FILE.chmod(0o644)

    assert config.get("api_key") == KEY

    assert _mode(config_home) == 0o700
    assert _mode(config.CONFIG_FILE) == 0o600


def test_saving_tightens_an_existing_world_readable_file(config_home):
    config_home.mkdir(mode=0o755)
    config_home.chmod(0o755)
    config.CONFIG_FILE.write_text("{}\n")
    config.CONFIG_FILE.chmod(0o644)

    config.save({"api_key": KEY})

    assert _mode(config_home) == 0o700
    assert _mode(config.CONFIG_FILE) == 0o600


def test_update_check_cache_creates_the_directory_owner_only(config_home):
    from datetime import datetime, timezone

    from beakr_cli import updates

    updates._write_cache("9.9.9", datetime.now(timezone.utc))

    assert _mode(config_home) == 0o700


def test_failed_login_does_not_echo_the_api_key(config_home, monkeypatch):
    from beakr_cli.commands import auth

    def reject(_path, *args, **kwargs):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(auth, "api_get", reject)
    result = CliRunner().invoke(auth.app, ["login", "--api-key", KEY])

    assert result.exit_code == 1
    assert KEY not in result.output
    assert config.get("api_key") is None
