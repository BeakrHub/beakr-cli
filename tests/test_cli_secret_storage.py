"""Where the CLI keeps a saved API key (security, B4).

ROOT CAUSE: ``beakr auth login --api-key`` saved the key in plain text in
``~/.beakr/config.json``, written with ``write_text`` (0644) in a 0755
directory, so any local user could read it (released 0.3.2 too). The key now
goes to the token store beside sign-ins (keychain, else ``credentials`` at
0600; the file store here, as BEAKR_NO_KEYRING forces), ``config.json`` holds
settings only and is written atomically at 0600 in a 0700 home, and a key an
older CLI left in ``config.json`` is moved into the store on the next run.
"""

from __future__ import annotations

import json
import stat

import pytest
import respx
from beakr_sdk.credentials import TokenStore, beakr_home, resolve_credential
from typer.testing import CliRunner

from beakr_cli import config
from beakr_cli.main import app

API = "https://api.test"


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_settings_are_written_0600_in_a_0700_home():
    config.set_key("project_id", "p1")
    assert _mode(config.config_file()) == 0o600
    assert _mode(beakr_home()) == 0o700


def test_config_json_refuses_a_secret():
    with pytest.raises(ValueError, match="secrets"):
        config.save({"api_key": "bk_live_x"})


def test_config_follows_beakr_home_like_the_sdk(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "profile"))
    config.set_key("api_url", API)
    assert config.config_file() == tmp_path / "profile" / "config.json"
    from beakr_sdk.credentials import read_cli_config

    assert read_cli_config()["api_url"] == API


@respx.mock
def test_a_key_an_older_cli_left_in_config_json_moves_to_the_store():
    home = beakr_home()
    home.mkdir(parents=True)
    legacy = home / "config.json"
    legacy.write_text(json.dumps({"api_key": "bk_live_legacy", "api_url": API}))
    legacy.chmod(0o644)
    respx.get(f"{API}/v1/me").respond(json={"user": {"display_name": "Ada"}})

    result = CliRunner().invoke(app, ["auth", "status", "--json"])

    assert result.exit_code == 0, result.output
    assert "bk_live_legacy" not in legacy.read_text()
    assert json.loads(legacy.read_text()) == {"api_url": API}
    assert _mode(legacy) == 0o600
    assert TokenStore().load(API).access_token == "bk_live_legacy"
    credential = resolve_credential(base_url=API)
    assert credential.source == "cli_config" and credential.token == "bk_live_legacy"
    assert _mode(home / "credentials") == 0o600


def test_migration_keeps_a_newer_sign_in_for_the_same_api():
    from beakr_sdk.credentials import StoredTokens

    TokenStore().save(StoredTokens(access_token="bk_at_new", refresh_token="rt", base_url=API))
    config.ensure_dir()
    config.config_file().write_text(json.dumps({"api_key": "bk_live_old", "api_url": API}))
    assert config.migrate_secrets() is True
    assert TokenStore().load(API).access_token == "bk_at_new"
    assert "api_key" not in json.loads(config.config_file().read_text())
