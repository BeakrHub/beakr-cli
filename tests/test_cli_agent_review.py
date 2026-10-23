"""``beakr kb agent-review show|set`` over the SDK's ``knowledge.agent_review``.

Engine #1717/#1727 let an organization or a project require a person to
accept what API keys and tokens write. ``show`` names the policy in force and
whether the organization or the project sets each field; ``set`` changes the
organization's policy, or a project's own (``--follow-org`` drops it). A
credential that may not change it (403 ``session_required``) exits 6 with a
hint that names the key a person can create, not only the web app.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.errors import EXIT_PERSON_REVIEW
from beakr_cli.main import app as published_app
from beakr_cli.main import internal_app as app

API = "https://api.test"
PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
ORG_POLICY = {
    "agent_proposals_need_a_person": True,
    "agent_uploads_need_a_person": False,
    "capture_auto_approve_allowed": False,
}


@pytest.fixture(autouse=True)
def api_env(isolated_home, monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _settings(project_id: str | None = None, override: dict | None = None) -> dict:
    effective = dict(ORG_POLICY)
    for field, value in (override or {}).items():
        if value is not None:
            effective[field] = value
    return {
        "available": True,
        "org": ORG_POLICY,
        "project_id": project_id,
        "project_override": override,
        "effective": effective,
    }


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


@respx.mock
def test_show_names_where_each_field_comes_from() -> None:
    route = respx.get(f"{API}/v1/knowledge/agent-review").respond(
        json=_settings(PROJECT, {"agent_uploads_need_a_person": True})
    )

    result = CliRunner().invoke(app, ["kb", "agent-review", "show", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["project_id"] == PROJECT
    lines = dict(line.split("\t", 1) for line in result.stdout.splitlines())
    assert lines["agent_proposals_need_a_person"] == ("effective=on\tfrom=org\torg=on\tproject=-")
    assert lines["agent_uploads_need_a_person"] == (
        "effective=on\tfrom=project\torg=off\tproject=on"
    )


@respx.mock
def test_set_changes_only_the_organizations_fields_given() -> None:
    route = respx.put(f"{API}/v1/knowledge/agent-review").respond(json=_settings())

    result = CliRunner().invoke(
        app, ["kb", "agent-review", "set", "--proposals", "on", "--capture-auto-approve", "unset"]
    )

    assert result.exit_code == 0, result.output
    assert _body(route) == {
        "agent_proposals_need_a_person": True,
        "capture_auto_approve_allowed": None,
    }


@respx.mock
def test_set_with_a_project_changes_its_own_policy() -> None:
    route = respx.put(f"{API}/v1/knowledge/agent-review/projects/{PROJECT}").respond(
        json=_settings(PROJECT, {"agent_uploads_need_a_person": True})
    )

    result = CliRunner().invoke(
        app, ["kb", "agent-review", "set", "-P", PROJECT, "--uploads", "on"]
    )

    assert result.exit_code == 0, result.output
    assert _body(route) == {"policy": {"agent_uploads_need_a_person": True}}
    assert "from=project" in result.stdout


@respx.mock
def test_follow_org_drops_the_projects_policy() -> None:
    route = respx.put(f"{API}/v1/knowledge/agent-review/projects/{PROJECT}").respond(
        json=_settings(PROJECT)
    )

    result = CliRunner().invoke(app, ["kb", "agent-review", "set", "-P", PROJECT, "--follow-org"])

    assert result.exit_code == 0, result.output
    assert _body(route) == {"policy": None}


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--follow-org"], "give the project with -P"),
        (["-P", PROJECT, "--follow-org", "--uploads", "on"], "without field options"),
        ([], "Give a field to change"),
        (["--proposals", "yes"], "--proposals takes on, off or unset"),
    ],
)
def test_set_refuses_what_it_cannot_send(argv: list[str], message: str) -> None:
    result = CliRunner().invoke(app, ["kb", "agent-review", "set", *argv])

    assert result.exit_code == 2, result.output
    assert message in result.output


def _session_required() -> httpx.Response:
    message = "This needs a signed-in person."
    return httpx.Response(
        403,
        json={
            "detail": {"code": "session_required", "message": message},
            "error": {"code": "session_required", "message": message},
        },
    )


@respx.mock
def test_a_credential_that_may_not_change_it_exits_6_naming_the_reviewer_key() -> None:
    respx.put(f"{API}/v1/knowledge/agent-review").mock(return_value=_session_required())

    result = CliRunner().invoke(app, ["kb", "agent-review", "set", "--proposals", "on"])

    assert result.exit_code == EXIT_PERSON_REVIEW, result.output
    text = " ".join(result.stderr.split())
    assert "proposals:accept scope (BEAKR_API_KEY)" in text


@respx.mock
def test_the_refusal_under_json_is_one_error_object() -> None:
    respx.put(f"{API}/v1/knowledge/agent-review").mock(return_value=_session_required())

    result = CliRunner().invoke(app, ["kb", "agent-review", "set", "--proposals", "on", "--json"])

    assert result.exit_code == EXIT_PERSON_REVIEW, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "session_required"
    assert error["exit_code"] == EXIT_PERSON_REVIEW
    assert "proposals:accept" in error["fix"]


def test_the_published_cli_has_the_agent_review_and_files_approve_commands() -> None:
    # #64 added `files approve` to the full tree only; the entry point lacked it.
    for argv in (
        ["kb", "agent-review", "show", "--help"],
        ["kb", "agent-review", "set", "--help"],
        ["files", "approve", "--help"],
    ):
        result = CliRunner().invoke(published_app, argv)
        assert result.exit_code == 0, (argv, result.output)


@respx.mock
@pytest.mark.parametrize("mode", ["human", "compact"])
def test_show_says_when_agent_review_is_not_available(monkeypatch, mode: str) -> None:
    # 0.1.9 qualification: only --json carried available:false; plain show was silent.
    monkeypatch.setenv("BEAKR_OUTPUT", mode)
    respx.get(f"{API}/v1/knowledge/agent-review").respond(json={**_settings(), "available": False})

    result = CliRunner().invoke(app, ["kb", "agent-review", "show"])

    assert result.exit_code == 0, result.output
    assert "Agent review is not available on this installation." in result.stderr


@respx.mock
@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["kb", "agent-review", "set", "--proposals", "on"], "feature_disabled"),
        # An engine before 0.1.10 answered the organization's write a bare not_found.
        (["kb", "agent-review", "set", "--proposals", "on"], "not_found"),
        (["kb", "agent-review", "set", "-P", PROJECT, "--uploads", "on"], "feature_disabled"),
    ],
)
def test_a_change_where_it_is_not_available_says_an_administrator_must_enable_it(
    argv: list[str], code: str
) -> None:
    # The generic not_found hint ("check the id or name: list the collection
    # first") sent a reviewer looking for a project that exists.
    respx.put(url__regex=rf"{API}/v1/knowledge/agent-review.*").respond(
        404,
        json={"error": {"code": code, "message": "Not available.", "fix": "Check the id or name."}},
    )

    result = CliRunner().invoke(app, argv)

    assert result.exit_code == 4, result.output
    text = " ".join(result.stderr.split())
    assert "Agent review is not available on this installation." in text
    assert "administrator" in text
    assert "Check the id or name" not in text

    as_json = CliRunner().invoke(app, [*argv, "--json"])
    error = json.loads(as_json.stdout)["error"]
    assert (error["code"], error["exit_code"]) == ("feature_disabled", 4)
