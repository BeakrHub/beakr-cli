"""A dev identity never outranks a real credential, and a saved one is origin-bound.

Root cause these guard: ``resolve_credential`` checked the dev identity before
``BEAKR_API_KEY`` and never looked at its origin, so a leftover
``beakr auth dev`` against http://localhost:8000 was sent to production in
place of the user's API key or login (SPEC-442).
"""

from __future__ import annotations

import json
import time

import pytest
from beakr_sdk import AuthenticationError
from beakr_sdk.credentials import (
    StoredTokens,
    TokenStore,
    beakr_home,
    read_cli_config,
    resolve_credential,
)
from typer.testing import CliRunner

from beakr_cli.main import app

PROD = "https://api.thebeakr.com"
LOCAL = "http://localhost:8000"


def _write_config(data: dict) -> None:
    home = beakr_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps(data))


def _saved_dev_identity(**extra: str) -> dict:
    return {"dev_identity_id": "id-dev", "dev_email": "dev@example.test", **extra}


def test_env_api_key_wins_over_a_saved_dev_identity(monkeypatch) -> None:
    _write_config({**_saved_dev_identity(dev_origin=LOCAL), "api_url": LOCAL})
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_env")
    for base_url in (LOCAL, PROD):
        credential = resolve_credential(base_url=base_url)
        assert credential.source == "env"
        assert credential.token == "bk_live_env"


def test_env_api_key_wins_over_an_env_dev_identity(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "id-env")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "env@example.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_env")
    assert resolve_credential(base_url=LOCAL).source == "env"


def test_a_localhost_dev_identity_is_never_sent_to_production() -> None:
    _write_config({**_saved_dev_identity(dev_origin=LOCAL), "api_url": LOCAL})
    with pytest.raises(AuthenticationError):
        resolve_credential(base_url=PROD)


def test_a_localhost_dev_identity_does_not_shadow_a_production_login() -> None:
    _write_config({**_saved_dev_identity(dev_origin=LOCAL), "api_url": LOCAL})
    TokenStore().save(
        StoredTokens(
            access_token="bk_at_prod",
            refresh_token="bk_rt_prod",
            expires_at=time.time() + 3600,
            base_url=PROD,
        )
    )
    credential = resolve_credential(base_url=PROD)
    assert credential.source == "cli_tokens"
    assert credential.tokens.access_token == "bk_at_prod"
    assert resolve_credential(base_url=LOCAL).source == "dev"


def test_a_saved_dev_identity_applies_only_to_its_own_origin() -> None:
    _write_config(_saved_dev_identity(dev_origin="http://localhost:8001"))
    assert resolve_credential(base_url="http://localhost:8001/").source == "dev"
    with pytest.raises(AuthenticationError):
        resolve_credential(base_url=LOCAL)
    with pytest.raises(AuthenticationError):
        resolve_credential(base_url="http://127.0.0.1:8001")


def test_a_saved_dev_identity_for_a_non_local_dev_server_applies_there() -> None:
    _write_config(_saved_dev_identity(dev_origin="http://dev-box.internal:8000"))
    assert resolve_credential(base_url="http://dev-box.internal:8000").source == "dev"
    with pytest.raises(AuthenticationError):
        resolve_credential(base_url=PROD)


@pytest.mark.parametrize("base_url", [LOCAL, "http://127.0.0.1:8042", "http://localhost"])
def test_a_legacy_dev_identity_without_an_origin_applies_to_loopback(base_url) -> None:
    # Saved before `beakr auth dev` recorded dev_origin: it always targeted a
    # local server, so it keeps working there.
    _write_config(_saved_dev_identity())
    credential = resolve_credential(base_url=base_url)
    assert credential.source == "dev"
    assert credential.auth_headers()["X-Identity-Id"] == "id-dev"


@pytest.mark.parametrize("base_url", [PROD, "https://api-sandbox.thebeakr.com"])
def test_a_legacy_dev_identity_without_an_origin_never_applies_off_loopback(base_url) -> None:
    _write_config({**_saved_dev_identity(), "api_key": "bk_live_saved", "api_url": base_url})
    credential = resolve_credential(base_url=base_url)
    assert credential.source == "cli_config"
    assert credential.token == "bk_live_saved"


def test_an_env_dev_identity_is_the_callers_explicit_choice(monkeypatch) -> None:
    # Like BEAKR_API_KEY, the environment pair is not origin-bound: eval
    # harnesses point it at dev-mode servers that are not on loopback.
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "id-env")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "env@example.test")
    _write_config(_saved_dev_identity(dev_origin=LOCAL))
    credential = resolve_credential(base_url="https://eval-dev.test")
    assert credential.source == "dev"
    assert credential.auth_headers()["X-Identity-Id"] == "id-env"
    assert credential.auth_headers()["X-Email"] == "env@example.test"


def test_auth_dev_saves_the_origin_and_the_dev_flow_still_works_for_localhost() -> None:
    result = CliRunner().invoke(
        app,
        ["auth", "dev", "--identity", "id-cli", "--email", "cli@example.test", "--json"],
    )
    assert result.exit_code == 0, result.output
    config = read_cli_config()
    assert config["dev_origin"] == LOCAL
    credential = resolve_credential()
    assert credential.source == "dev"
    assert credential.auth_headers()["X-Identity-Id"] == "id-cli"
    assert credential.auth_headers()["X-Email"] == "cli@example.test"
    with pytest.raises(AuthenticationError):
        resolve_credential(base_url=PROD)


def test_auth_dev_with_a_custom_api_url_binds_to_that_origin() -> None:
    result = CliRunner().invoke(
        app,
        [
            "auth",
            "dev",
            "--identity",
            "id-cli",
            "--email",
            "cli@example.test",
            "--api-url",
            "http://127.0.0.1:8090/",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert read_cli_config()["dev_origin"] == "http://127.0.0.1:8090"
    assert resolve_credential().source == "dev"
    with pytest.raises(AuthenticationError):
        resolve_credential(base_url=LOCAL)


def test_logout_clears_the_saved_dev_origin() -> None:
    runner = CliRunner()
    runner.invoke(app, ["auth", "dev", "--identity", "i", "--email", "e@x.test", "--json"])
    assert "dev_origin" in read_cli_config()
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0, result.output
    assert "dev_origin" not in read_cli_config()


def test_an_ipv6_loopback_origin_keeps_its_brackets_and_counts_as_local():
    # normalize_origin dropped the brackets urlsplit strips from IPv6 literals,
    # producing "http://::1:8000", which no longer parses as host + port.
    from beakr_sdk.credentials import _is_loopback_origin, normalize_origin

    origin = normalize_origin("http://[::1]:8000/")
    assert origin == "http://[::1]:8000"
    assert normalize_origin(origin) == origin
    assert _is_loopback_origin(origin)
    assert normalize_origin("https://[2001:DB8::1]") == "https://[2001:db8::1]"
