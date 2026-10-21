"""`members rm` says whether it removed anyone.

Root cause: every removal route (``DELETE /v1/projects/{id}/members/{type}/{id}``,
``DELETE /v1/labels/{id}/members/{user}``, ``DELETE /v1/organizations/{id}/members/{user}``)
is idempotent and answers 204 whether or not the membership existed, and the
three ``rm`` commands printed ``removed: true`` / "Removed X." from that answer
alone. Removing someone who was never a member reported a removal. Each command
now reads the member list first and reports ``removed: false`` ("nothing to
remove", exit 0, as other no-ops are) when there was no membership; the DELETE
itself is sent as before.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
runner = CliRunner()

MEMBERS = [
    {"principal_id": "u1", "principal_type": "user", "role": "owner", "name": "Ada"},
    {"principal_id": "g1", "principal_type": "group", "role": "viewer", "name": "Chemists"},
    {
        "principal_id": "u3",
        "principal_type": "user",
        "role": "viewer",
        "name": "Bo",
        "inherited_from": "label:Medicinal Chemistry",
    },
]


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _project(members=MEMBERS):
    respx.get(f"{API}/v1/projects").respond(
        200, json=[{"id": "p1", "slug": "assays", "name": "Assays"}]
    )
    respx.get(f"{API}/v1/projects/p1/members").respond(200, json=members)


@respx.mock
def test_projects_members_rm_of_a_non_member_reports_nothing_removed(monkeypatch):
    _project()
    delete = respx.delete(f"{API}/v1/projects/p1/members/user/u9").respond(204)

    result = runner.invoke(app, ["projects", "members", "rm", "assays", "u9", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["removed"] is False
    assert delete.called  # still idempotent: the removal is sent either way

    monkeypatch.setenv("BEAKR_OUTPUT", "human")
    shown = runner.invoke(app, ["projects", "members", "rm", "assays", "u9", "--yes"])
    assert shown.exit_code == 0, shown.output
    assert "u9 is not a member of Assays; nothing to remove." in shown.stderr
    assert "Removed" not in shown.output


@respx.mock
def test_projects_members_rm_of_a_member_reports_the_removal():
    _project()
    respx.delete(f"{API}/v1/projects/p1/members/user/U1").respond(204)

    result = runner.invoke(app, ["projects", "members", "rm", "assays", "U1", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["removed"] is True


@respx.mock
def test_projects_members_rm_type_group_checks_the_group_membership():
    _project()
    respx.delete(f"{API}/v1/projects/p1/members/group/g1").respond(204)
    respx.delete(f"{API}/v1/projects/p1/members/group/u1").respond(204)

    group = runner.invoke(
        app, ["projects", "members", "rm", "assays", "g1", "--type", "group", "--yes", "--json"]
    )
    # u1 is a user member, not a group one: removing group u1 removes nothing.
    wrong_type = runner.invoke(
        app, ["projects", "members", "rm", "assays", "u1", "--type", "group", "--yes", "--json"]
    )

    assert json.loads(group.stdout)["removed"] is True
    assert wrong_type.exit_code == 0, wrong_type.output
    assert json.loads(wrong_type.stdout)["removed"] is False


@respx.mock
def test_projects_members_rm_of_label_access_names_the_label(monkeypatch):
    _project()
    respx.delete(f"{API}/v1/projects/p1/members/user/u3").respond(204)

    result = runner.invoke(app, ["projects", "members", "rm", "assays", "u3", "--yes", "--json"])
    assert json.loads(result.stdout) == {
        "project_id": "p1",
        "principal_id": "u3",
        "principal_type": "user",
        "removed": False,
        "inherited_from": "label:Medicinal Chemistry",
    }

    monkeypatch.setenv("BEAKR_OUTPUT", "human")
    shown = runner.invoke(app, ["projects", "members", "rm", "assays", "u3", "--yes"])
    assert "only through label:Medicinal Chemistry" in shown.stderr


@respx.mock
def test_labels_members_rm_of_a_non_member_reports_nothing_removed(monkeypatch):
    respx.get(f"{API}/v1/labels").respond(200, json=[{"id": "l1", "name": "Chemists"}])
    respx.get(f"{API}/v1/labels/l1/members").respond(200, json=[{"user_id": "u1"}])
    delete = respx.delete(f"{API}/v1/labels/l1/members/u9").respond(204)
    respx.delete(f"{API}/v1/labels/l1/members/u1").respond(204)

    missing = runner.invoke(app, ["labels", "members", "rm", "Chemists", "u9", "--json"])
    present = runner.invoke(app, ["labels", "members", "rm", "Chemists", "u1", "--json"])

    assert missing.exit_code == 0, missing.output
    assert json.loads(missing.stdout)["removed"] is False
    assert delete.called
    assert json.loads(present.stdout)["removed"] is True

    monkeypatch.setenv("BEAKR_OUTPUT", "human")
    shown = runner.invoke(app, ["labels", "members", "rm", "Chemists", "u9"])
    assert "u9 is not on the label; nothing to remove." in shown.stderr


@respx.mock
def test_org_members_rm_of_a_non_member_reports_nothing_removed():
    respx.get(f"{API}/v1/organizations/org-9/members").respond(
        200, json={"data": [{"user_id": "u1"}], "has_more": False, "next_cursor": None}
    )
    delete = respx.delete(f"{API}/v1/organizations/org-9/members/u9").respond(204)
    respx.delete(f"{API}/v1/organizations/org-9/members/u1").respond(204)

    missing = runner.invoke(app, ["members", "rm", "u9", "--org", "org-9", "--yes", "--json"])
    piped = runner.invoke(app, ["members", "rm", "u9", "--org", "org-9", "--yes"])
    present = runner.invoke(app, ["members", "rm", "u1", "--org", "org-9", "--yes"])

    assert missing.exit_code == 0, missing.output
    assert json.loads(missing.stdout) == {"user_id": "u9", "removed": False}
    assert delete.called
    assert piped.stdout.split() == ["u9", "removed=false"]
    assert present.stdout.split() == ["u1", "removed=true"]
