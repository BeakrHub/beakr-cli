"""A self-hosted build has no hosted default (deep QA, 16 Nov).

Root cause: with no ``--api-url``, ``BEAKR_BASE_URL`` or saved ``api_url``, the bundled
CLI fell back to Beakr's hosted API: ``beakr auth login`` opened Beakr's hosted sign-in
page and ``--with-api-key`` sent the installation's key to ``api.thebeakr.com``. Now
nothing is sent anywhere until a deployment URL is set; the refusal says how to set it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.deployment import BASE_URL_REQUIRED, BASE_URL_REQUIRED_MESSAGE
from beakr_cli.main import app

SRC = Path(__file__).resolve().parents[1] / "src" / "beakr_cli"
runner = CliRunner()


def _text(result) -> str:
    return " ".join(result.output.replace("│", " ").split())


@pytest.fixture
def anywhere():
    """Every request, to any host: none may be made."""
    with respx.mock(assert_all_called=False) as router:
        yield router.route().respond(500)


def test_no_source_names_beakrs_hosted_service():
    named = [
        f"{path.relative_to(SRC)}:{number}"
        for path in SRC.rglob("*.py")
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"thebeakr\.com", line)
    ]
    assert named == []


def test_a_command_with_no_deployment_url_is_refused_before_any_request(anywhere, monkeypatch):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_from_the_installation")

    result = runner.invoke(app, ["whoami"])

    assert result.exit_code == 3, result.output
    assert BASE_URL_REQUIRED_MESSAGE in _text(result)
    assert "--with-api-key" in _text(result)
    assert not anywhere.called


def test_api_key_login_with_no_url_sends_the_key_nowhere(anywhere):
    result = runner.invoke(app, ["auth", "login", "--with-api-key"], input="bk_live_secret\n")

    assert result.exit_code == 3, result.output
    text = _text(result)
    assert BASE_URL_REQUIRED_MESSAGE in text
    assert "beakr auth login --api-url https://<your Beakr host> --with-api-key" in text
    assert not anywhere.called
    assert "bk_live_secret" not in result.output


def test_browser_login_with_no_url_says_how_to_sign_in_with_a_key(anywhere):
    result = runner.invoke(app, ["auth", "login", "--no-browser"])

    assert result.exit_code == 3, result.output
    assert "This installation signs in with an API key" in _text(result)
    assert not anywhere.called


def test_hosted_environments_are_refused(anywhere):
    result = runner.invoke(app, ["auth", "login", "--env", "prod", "--with-api-key"], input="k\n")

    assert result.exit_code == 2, result.output
    assert "--api-url" in _text(result)
    assert not anywhere.called


def test_auth_status_says_no_url_is_set(anywhere, monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)

    result = runner.invoke(app, ["auth", "status"])

    # Not signed in (exit 3, as before), and no request to find that out.
    assert result.exit_code == 3, result.output
    assert "not set (--api-url / BEAKR_BASE_URL)" in _text(result)
    assert "--with-api-key" in _text(result)
    assert not anywhere.called


def test_the_sdk_clients_refuse_without_a_url():
    import asyncio

    from beakr_sdk.errors import AuthenticationError

    from beakr_cli.sdk import get_async_sdk, get_sdk

    with pytest.raises(AuthenticationError) as refused:
        get_sdk()
    assert refused.value.code == BASE_URL_REQUIRED
    with pytest.raises(AuthenticationError):
        asyncio.run(_open(get_async_sdk))


async def _open(factory):
    async with factory():
        pass


def test_a_set_url_is_used(monkeypatch):
    monkeypatch.setenv("BEAKR_BASE_URL", "https://beakr.example.com/")
    from beakr_cli.app_url import app_url
    from beakr_cli.sdk import get_sdk

    with get_sdk() as sdk:
        assert str(sdk.base_url).rstrip("/") == "https://beakr.example.com"
    monkeypatch.setattr("beakr_cli.app_url._reported_app_url", lambda base: None)
    # With no app URL reported or saved, the guess is the deployment itself, never
    # Beakr's hosted app.
    assert app_url().url == "https://beakr.example.com"
