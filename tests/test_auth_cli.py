import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app


def test_whoami_reports_environment_scope_over_stored_config(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_ORG_ID", "org-from-env")
    monkeypatch.setenv("BEAKR_PROJECT_ID", "project-from-env")
    monkeypatch.setattr(
        "beakr_cli.commands.auth._me",
        lambda: {
            "user": {"display_name": "Sarah Chen"},
            "personal_org": {"name": "Example University"},
        },
    )
    monkeypatch.setattr("beakr_cli.commands.auth.config.get", lambda _key: "stale-config")

    result = CliRunner().invoke(app, ["auth", "whoami"])

    assert result.exit_code == 0
    assert "Org override: org-from-env" in result.output
    assert "Scope: project project-from-env" in result.output
    assert "Scope: none" not in result.output


@respx.mock
@pytest.mark.parametrize("command", [["whoami"], ["auth", "whoami"]])
def test_whoami_exits_with_the_auth_code_when_the_credential_is_refused(
    monkeypatch, command: list[str]
) -> None:
    # Root cause: _whoami turned every failure into exit 1, so an expired or
    # revoked token was indistinguishable from an outage; the documented
    # authentication exit code is 3.
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_revoked")
    respx.get("https://api.test/v1/me").respond(
        401, json={"error": {"code": "unauthorized", "message": "The API key was revoked."}}
    )

    result = CliRunner().invoke(app, command)

    assert result.exit_code == 3, result.output
    assert "revoked" in result.output


def test_whoami_without_any_credential_exits_with_the_auth_code() -> None:
    result = CliRunner().invoke(app, ["whoami"])

    assert result.exit_code == 3, result.output


@respx.mock
def test_whoami_still_exits_1_when_the_api_fails(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live")
    respx.get("https://api.test/v1/me").respond(500, json={"detail": "boom"})
    monkeypatch.setattr("time.sleep", lambda _s: None)

    result = CliRunner().invoke(app, ["whoami"])

    assert result.exit_code == 1, result.output
