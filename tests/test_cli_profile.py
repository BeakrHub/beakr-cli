"""``beakr profile``: your claims, and project profile documents."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
CLAIMS = f"{API}/v1/me/profile"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


# -- profile -------------------------------------------------------------------


def _interrupt(_request):
    """The second connection: the person presses Ctrl-C while reconnecting."""
    raise KeyboardInterrupt


@respx.mock
def test_profile_show_lists_claims() -> None:
    respx.get(CLAIMS).mock(
        return_value=httpx.Response(
            200, json=[{"id": "c1", "layer": "identity", "content": "Runs platform"}]
        )
    )
    result = CliRunner().invoke(app, ["profile", "show", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["id"] == "c1"


@respx.mock
def test_profile_show_all_includes_inactive() -> None:
    route = respx.get(CLAIMS).mock(return_value=httpx.Response(200, json=[]))
    result = CliRunner().invoke(app, ["profile", "show", "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["include_inactive"] == "true"


@respx.mock
def test_profile_show_filters_and_groups_by_profile_layer(monkeypatch) -> None:
    """``--layer`` asks the server for those layers; claims group under them (SPEC-302)."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    route = respx.get(CLAIMS).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "c1",
                        "layer": "care_about",
                        "profile_layer": "goals",
                        "section": "goals",
                        "content": "Ship the assay pipeline",
                    },
                    {
                        "id": "c2",
                        "layer": "stated_commitment",
                        "profile_layer": "reminders",
                        "section": "commitments",
                        "content": "Send the Q3 numbers to Ali",
                    },
                ],
                "has_more": False,
            },
        )
    )
    result = CliRunner().invoke(
        app, ["profile", "show", "--layer", "goals", "--layer", "reminders"]
    )
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params.get_list("layer") == ["goals", "reminders"]
    assert "goals" in result.output and "reminders" in result.output
    assert "commitments" not in result.output


def test_profile_show_layer_is_for_your_own_profile() -> None:
    result = CliRunner().invoke(app, ["profile", "show", "--org", "--layer", "goals"])
    assert result.exit_code == 2


@respx.mock
def test_profile_confirm_and_dismiss_patch_the_claim() -> None:
    route = respx.patch(f"{API}/v1/me/profile/claims/c1").mock(
        return_value=httpx.Response(200, json={"id": "c1"})
    )
    runner = CliRunner()

    assert runner.invoke(app, ["profile", "confirm", "c1"]).exit_code == 0
    assert _body(route) == {"confirm": True}
    assert runner.invoke(app, ["profile", "dismiss", "c1"]).exit_code == 0
    assert _body(route) == {"dismiss": True}


_CHANGES = [
    {"claim_id": "c1", "action": "confirm"},
    {"claim_id": "c2", "action": "dismiss"},
    {"claim_id": "c3", "action": "edit", "content": "Writes in British English."},
]


@respx.mock
def test_profile_apply_sends_every_change_in_one_patch(tmp_path, monkeypatch) -> None:
    """One PATCH /v1/me/profile carries the whole batch (SPEC-302)."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    route = respx.patch(CLAIMS).mock(
        return_value=httpx.Response(
            200,
            json={
                "claims": [
                    {"id": "c1", "content": "Tables", "profile_layer": "preferences"},
                    {"id": "c2", "content": "Long meetings", "profile_layer": "preferences"},
                    {"id": "c4", "content": "British English", "profile_layer": "preferences"},
                ]
            },
        )
    )
    changes = tmp_path / "changes.json"
    changes.write_text(json.dumps({"changes": _CHANGES}))

    result = CliRunner().invoke(app, ["profile", "apply", "--file", str(changes)])

    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert _body(route) == {"changes": _CHANGES}
    assert "Applied 3 change(s): 1 confirm, 1 dismiss, 1 edit." in result.output

    # The bare list is accepted too, and --json prints the answer as sent.
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: True)
    changes.write_text(json.dumps(_CHANGES))
    as_json = CliRunner().invoke(app, ["profile", "apply", "-f", str(changes), "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert _body(route) == {"changes": _CHANGES}
    assert [c["id"] for c in json.loads(as_json.output)["claims"]] == ["c1", "c2", "c4"]


@respx.mock
def test_profile_apply_reports_the_refused_change(tmp_path) -> None:
    respx.patch(CLAIMS).mock(
        return_value=httpx.Response(
            404,
            json={
                "error": {
                    "code": "not_found",
                    "message": "changes[1]: profile claim not found. No change was applied.",
                    "details": {"index": 1, "claim_id": "c2"},
                }
            },
        )
    )
    changes = tmp_path / "changes.json"
    changes.write_text(json.dumps(_CHANGES))

    result = CliRunner().invoke(app, ["profile", "apply", "--file", str(changes)])

    assert result.exit_code == 4
    assert "changes[1]" in result.output and "No change was applied" in result.output


@respx.mock
def test_profile_apply_checks_the_file_before_calling(tmp_path) -> None:
    route = respx.patch(CLAIMS)
    changes = tmp_path / "changes.json"
    runner = CliRunner()

    for bad in (
        "not json",
        "[]",
        '[{"claim_id": "c1", "action": "approve"}]',
        '[{"action": "confirm"}]',
    ):
        changes.write_text(bad)
        assert runner.invoke(app, ["profile", "apply", "--file", str(changes)]).exit_code == 2
    missing = str(tmp_path / "nope.json")
    assert runner.invoke(app, ["profile", "apply", "--file", missing]).exit_code == 2
    assert route.call_count == 0


@respx.mock
def test_project_profile_absent_is_reported_not_an_error() -> None:
    respx.get(f"{API}/v1/knowledge/profiles/project/p1").mock(
        return_value=httpx.Response(200, json=None)
    )
    result = CliRunner().invoke(app, ["profile", "show", "--project", "p1"])
    assert result.exit_code == 0, result.output
    assert "no knowledge profile" in result.output


def test_profile_forget_without_yes_is_a_usage_error_when_not_interactive() -> None:
    result = CliRunner().invoke(app, ["profile", "forget", "c1"])
    assert result.exit_code == 2


@respx.mock
def test_profile_forget_with_yes_deletes() -> None:
    route = respx.delete(f"{API}/v1/me/profile/claims/c1").mock(return_value=httpx.Response(204))
    result = CliRunner().invoke(app, ["profile", "forget", "c1", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


@respx.mock
def test_profile_create_starts_a_project_profile_idempotently() -> None:
    route = respx.post(f"{API}/v1/knowledge/profiles").mock(
        return_value=httpx.Response(201, json={"id": "kp1", "profile_type": "project"})
    )
    result = CliRunner().invoke(
        app,
        ["profile", "create", "--project", "p1", "-d", "Assay work", "--goal", "Ship v2"],
    )
    assert result.exit_code == 0, result.output
    request = route.calls.last.request
    assert json.loads(request.content) == {
        "profile_type": "project",
        "project_id": "p1",
        "description": "Assay work",
        "goals": ["Ship v2"],
        "focus_areas": [],
    }
    assert request.headers.get("Idempotency-Key")


def test_profile_create_needs_a_scope() -> None:
    assert CliRunner().invoke(app, ["profile", "create"]).exit_code == 2


@pytest.mark.parametrize("command", ["show", "create"])
@respx.mock
def test_org_profile_commands_are_retired_without_api_calls(command):
    # The server no longer serves org profiles; reject stale invocations locally.
    result = CliRunner().invoke(app, ["profile", command, "--org"])
    assert result.exit_code == 2
    assert not respx.calls
