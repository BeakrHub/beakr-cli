from typer.testing import CliRunner

from beakr_cli.main import app


def test_whoami_reports_environment_scope_over_stored_config(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_ORG_ID", "org-from-env")
    monkeypatch.setenv("BEAKR_PROJECT_ID", "project-from-env")
    monkeypatch.setattr(
        "beakr_cli.commands.auth.api_get",
        lambda _path: {
            "user": {"display_name": "Sarah Chen"},
            "personal_org": {"name": "Yale University"},
        },
    )
    monkeypatch.setattr("beakr_cli.commands.auth.config.get", lambda _key: "stale-config")

    result = CliRunner().invoke(app, ["auth", "whoami"])

    assert result.exit_code == 0
    assert "Org override: org-from-env" in result.output
    assert "Scope: project project-from-env" in result.output
    assert "Scope: none" not in result.output
