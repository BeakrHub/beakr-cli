"""The C039 parity commands: changes, watch, audit export, profile edit, integration
scope, org show, project members, file versions, kb entities/history, memory and
skill proposals (mocked engine routes)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.commands.activity import parse_since
from beakr_cli.main import internal_app as app

API = "https://api.test"
PAGE_ID = "11111111-1111-1111-1111-111111111111"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def rendered(monkeypatch):
    """Render tables, as in a terminal, instead of JSON for a pipe."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _active_org() -> None:
    respx.get(f"{API}/v1/me/organization").respond(
        200, json={"id": "org1", "name": "Acme Lab", "slug": "acme", "phi_mode": False}
    )


# -- changes / watch / audit ---------------------------------------------------


def test_parse_since_accepts_durations_and_dates():
    now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    assert parse_since("2d", now=now) == datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    assert parse_since("2026-10-01") == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_changes_rejects_an_unreadable_since():
    result = runner.invoke(app, ["changes", "--since", "last tuesday"])
    assert result.exit_code == 2


_RECONNECTS: list[httpx.Request] = []


def _interrupt(request):
    """The second connection: the person presses Ctrl-C while reconnecting."""
    _RECONNECTS.append(request)
    raise KeyboardInterrupt


@respx.mock
def test_changes_filters_on_the_server_and_follows_the_cursor(monkeypatch):
    # SPEC-358: since, agent and person are server-side filters on /v1/changes;
    # the CLI used to page the activity feed and filter by time itself.
    monkeypatch.setattr(
        "beakr_cli.commands.activity.parse_since",
        lambda value: datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    route = respx.get(f"{API}/v1/changes").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [{"id": "c1", "kind": "proposal.filed", "agent": "bot"}],
                    "next_cursor": "cur1",
                    "has_more": True,
                },
            ),
            httpx.Response(
                200,
                json={
                    "data": [{"id": "c2", "kind": "knowledge.commit", "agent": "bot"}],
                    "next_cursor": None,
                    "has_more": False,
                },
            ),
        ]
    )
    result = runner.invoke(
        app, ["changes", "--since", "4d", "--agent", "bot", "--kind", "proposal.filed", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert [e["id"] for e in json.loads(result.output)] == ["c1", "c2"]
    first, second = (call.request.url.params for call in route.calls)
    assert first["agent"] == "bot" and first["since"].startswith("2026-10-05")
    assert first.get_list("kind") == ["proposal.filed"]
    assert second["cursor"] == "cur1"


@respx.mock
def test_changes_for_a_project_passes_the_project(rendered):
    route = respx.get(f"{API}/v1/changes").respond(
        200,
        json={
            "data": [
                {
                    "id": "c1",
                    "created_at": "2026-10-08T10:00:00Z",
                    "kind": "knowledge.commit",
                    "agent": "compiler",
                    "summary": "Binding assay",
                }
            ],
            "next_cursor": None,
            "has_more": False,
        },
    )
    result = runner.invoke(app, ["changes", "--project", "p1"])
    assert result.exit_code == 0, result.output
    assert "Binding assay" in result.output
    assert route.calls.last.request.url.params["project_id"] == "p1"


# -- profile / integrations / org ----------------------------------------------


@respx.mock
def test_profile_edit_patches_the_claim():
    route = respx.patch(f"{API}/v1/me/profile/claims/cl1").respond(200, json={"id": "cl1"})
    result = runner.invoke(
        app, ["profile", "edit", "cl1", "--content", "Leads assay work", "--status", "active"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "content": "Leads assay work",
        "status": "active",
    }


def test_profile_edit_needs_a_change():
    result = runner.invoke(app, ["profile", "edit", "cl1"])
    assert result.exit_code == 2


@respx.mock
def test_integrations_scope_shows_and_restricts(rendered):
    path = f"{API}/v1/integrations/hosted/connectors/c1/scope"
    respx.get(path).respond(
        200, json={"mode": "restricted", "items": [{"id": "f1", "name": "Contracts"}]}
    )
    put = respx.put(path).respond(200, json={"mode": "restricted", "items": []})
    shown = runner.invoke(app, ["integrations", "scope", "c1"])
    assert shown.exit_code == 0, shown.output
    assert "Contracts" in shown.output
    restricted = runner.invoke(
        app, ["integrations", "scope", "c1", "--item", "folder:f1:Contracts", "--item", "label:L2"]
    )
    assert restricted.exit_code == 0, restricted.output
    # The engine takes "restricted" (not "selected") and needs id, type and name.
    assert json.loads(put.calls.last.request.content) == {
        "mode": "restricted",
        "items": [
            {"type": "folder", "id": "f1", "name": "Contracts"},
            {"type": "label", "id": "L2", "name": "L2"},
        ],
    }
    opened = runner.invoke(app, ["integrations", "scope", "c1", "--all"])
    assert opened.exit_code == 0, opened.output
    assert json.loads(put.calls.last.request.content) == {"mode": "all"}


def test_integrations_scope_rejects_a_bare_item_id():
    result = runner.invoke(app, ["integrations", "scope", "c1", "--item", "f1"])
    assert result.exit_code == 2


@respx.mock
def test_integrations_disconnect_is_rm():
    route = respx.delete(url__regex=rf"{API}/v1/integrations/.*c1").respond(204)
    result = runner.invoke(app, ["integrations", "disconnect", "c1", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


@respx.mock
def test_org_show_reads_the_active_org(rendered):
    _active_org()
    result = runner.invoke(app, ["org", "show"])
    assert result.exit_code == 0, result.output
    assert "Acme Lab" in result.output
    assert "PHI mode: off" in result.output


@respx.mock
def test_org_show_with_an_id():
    respx.get(f"{API}/v1/organizations/org2").respond(200, json={"id": "org2", "name": "Other"})
    result = runner.invoke(app, ["org", "show", "--org", "org2", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["name"] == "Other"


# -- projects members / files versions -----------------------------------------


def _projects() -> None:
    respx.get(f"{API}/v1/projects").respond(
        200, json=[{"id": "p1", "slug": "assays", "name": "Assays"}]
    )


@respx.mock
def test_projects_members_ls_and_add(rendered):
    _projects()
    respx.get(f"{API}/v1/projects/p1/members").respond(
        200, json=[{"name": "Ada", "email": "ada@x.test", "role": "owner", "principal_id": "u1"}]
    )
    add = respx.post(f"{API}/v1/projects/p1/members").respond(201, json={"principal_id": "u2"})
    listed = runner.invoke(app, ["projects", "members", "ls", "assays"])
    assert listed.exit_code == 0, listed.output
    assert "ada@x.test" in listed.output
    added = runner.invoke(app, ["projects", "members", "add", "Assays", "u2", "--role", "admin"])
    assert added.exit_code == 0, added.output
    assert json.loads(add.calls.last.request.content) == {
        "principal_id": "u2",
        "principal_type": "user",
        "role": "admin",
    }


# Root cause: `--role` was free text whose help named "editor", a role the API
# does not have (its roles are owner, admin, member and viewer), so the help led
# to a 400 from the server. The option takes the API's roles and refuses any
# other before a request is made.
@respx.mock
def test_projects_members_add_takes_only_the_apis_roles():
    _projects()
    add = respx.post(f"{API}/v1/projects/p1/members").respond(201, json={"principal_id": "u2"})

    refused = runner.invoke(app, ["projects", "members", "add", "assays", "u2", "--role", "editor"])
    shown = runner.invoke(app, ["projects", "members", "add", "--help"])

    assert refused.exit_code == 2, refused.output
    assert "editor" in refused.output
    assert not add.called
    help_text = " ".join(shown.output.split())
    assert "editor" not in help_text
    for role in ("owner", "admin", "member", "viewer"):
        assert role in help_text


# Root cause: `members add` sent role="member" when --role was not given, so
# re-adding someone demoted them (the last owner too). Without --role no role
# is sent: the server keeps an existing member's role (engine #1689).
@respx.mock
def test_projects_members_add_sends_no_role_unless_asked(monkeypatch):
    _projects()
    add = respx.post(f"{API}/v1/projects/p1/members").respond(
        201, json={"principal_id": "u1", "role": "owner"}
    )
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)

    added = runner.invoke(app, ["projects", "members", "add", "assays", "u1"])

    assert added.exit_code == 0, added.output
    assert json.loads(add.calls.last.request.content) == {
        "principal_id": "u1",
        "principal_type": "user",
    }
    assert "Added u1 to Assays as owner." in added.output


@respx.mock
def test_demoting_the_last_owner_reads_as_a_conflict_with_the_fix():
    _projects()
    message = "Cannot change the role of the last owner of the project"
    respx.post(f"{API}/v1/projects/p1/members").respond(
        409,
        json={
            "detail": {"code": "last_owner", "message": message},
            "error": {"code": "last_owner", "message": message},
        },
    )

    result = runner.invoke(app, ["projects", "members", "add", "assays", "u1", "--role", "member"])

    text = " ".join(result.output.split())
    assert result.exit_code == 5, result.output
    assert message in text
    assert "Make another member an owner first" in text
    assert "--role owner" in text


# Root cause: `members add` offered --type group, which the server refuses
# ("Group project memberships are no longer supported"), so the option only
# led to a failed request. add takes users only; the option is gone.
@respx.mock
def test_projects_members_add_offers_no_group_type():
    _projects()
    add = respx.post(f"{API}/v1/projects/p1/members").respond(201, json={"principal_id": "g1"})
    result = runner.invoke(app, ["projects", "members", "add", "assays", "g1", "--type", "group"])
    assert result.exit_code == 2
    assert "--type" in result.output
    assert not add.called


@respx.mock
def test_projects_members_rm_and_invite():
    _projects()
    respx.get(f"{API}/v1/projects/p1/members").respond(200, json=[{"principal_id": "u2"}])
    rm = respx.delete(f"{API}/v1/projects/p1/members/user/u2").respond(204)
    invite = respx.post(f"{API}/v1/projects/p1/invite").respond(201, json={"id": "i1"})
    removed = runner.invoke(app, ["projects", "members", "rm", "p1", "u2", "--yes"])
    assert removed.exit_code == 0, removed.output
    assert rm.called
    invited = runner.invoke(app, ["projects", "members", "invite", "p1", "bo@x.test"])
    assert invited.exit_code == 0, invited.output
    assert json.loads(invite.calls.last.request.content) == {
        "invitee_email": "bo@x.test",
        "role": "member",
    }


@respx.mock
def test_projects_members_unknown_project_is_not_found():
    _projects()
    result = runner.invoke(app, ["projects", "members", "ls", "nope"])
    assert result.exit_code == 4


@respx.mock
def test_files_versions_newest_first(rendered):
    respx.get(f"{API}/v1/artifacts/a1/versions").respond(
        200,
        json=[
            {"id": "v1", "version_no": 1, "size_bytes": 10},
            {"id": "v2", "version_no": 2, "size_bytes": 2048},
        ],
    )
    result = runner.invoke(app, ["files", "versions", "a1"])
    assert result.exit_code == 0, result.output
    assert result.output.index("v2") < result.output.index("v1")
    assert "2,048" in result.output


# -- kb entities / history -----------------------------------------------------


@respx.mock
def test_kb_entities_counts_and_filters_by_type(rendered):
    route = respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        200,
        json={
            "data": [
                {"id": "pg1", "title": "Ada Lovelace", "page_type": "person"},
                {"id": "pg2", "title": "Acme", "page_type": "organization"},
                {"id": "pg3", "title": "Bo", "page_type": "person"},
            ],
            "next_cursor": None,
            "has_more": False,
        },
    )
    result = runner.invoke(app, ["kb", "entities", "--project", "p1"])
    assert result.exit_code == 0, result.output
    assert "person 2" in result.output
    assert "organization 1" in result.output
    typed = runner.invoke(app, ["kb", "entities", "--type", "person", "--json"])
    assert typed.exit_code == 0, typed.output
    assert route.calls.last.request.url.params["page_type"] == "person"


@respx.mock
def test_kb_history_shows_a_pages_revisions():
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE_ID}").respond(
        200, json={"id": PAGE_ID, "title": "Binding assay"}
    )
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE_ID}/revisions").respond(
        200,
        json={
            "data": [{"revision": 3, "summary": "Added controls"}],
            "next_cursor": None,
            "has_more": False,
            "total": 1,
        },
    )
    result = runner.invoke(app, ["kb", "history", PAGE_ID, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["summary"] == "Added controls"


# -- memory / skills -----------------------------------------------------------


@respx.mock
def test_memory_ls_and_rm(rendered):
    respx.get(f"{API}/v1/me/memories").respond(
        200,
        json=[
            {"id": "m1", "kind": "fact", "content": "Uses metric units"},
            {"id": "m2", "kind": "preference", "content": "Short answers", "pinned": True},
        ],
    )
    delete = respx.delete(f"{API}/v1/me/memories/m1").respond(204)
    listed = runner.invoke(app, ["memory", "ls"])
    assert listed.exit_code == 0, listed.output
    assert listed.output.index("Short answers") < listed.output.index("Uses metric units")
    removed = runner.invoke(app, ["memory", "rm", "m1", "--yes"])
    assert removed.exit_code == 0, removed.output
    assert delete.called


def test_memory_rm_refuses_without_yes_when_not_interactive():
    result = runner.invoke(app, ["memory", "rm", "m1"])
    assert result.exit_code == 2


@respx.mock
def test_skills_edit_replaces_skill_md(tmp_path):
    respx.get(f"{API}/v1/skills/assay-qc").respond(
        200, json={"id": "sk1", "slug": "assay-qc", "owner": "me"}
    )
    respx.get(f"{API}/v1/skills/sk1/bundle").respond(
        200, json={"skill_id": "sk1", "slug": "assay-qc", "files": {}, "files_sha256": "abc"}
    )
    put = respx.put(f"{API}/v1/skills/sk1/bundle").respond(200, json={"skill_id": "sk1"})
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("# Assay QC\n")
    result = runner.invoke(app, ["skills", "edit", "assay-qc", "--file", str(skill_md)])
    assert result.exit_code == 0, result.output
    # One file overlaid on the rest, conditional on the bundle just read.
    assert json.loads(put.calls.last.request.content) == {
        "files": {"SKILL.md": "# Assay QC\n"},
        "replace": False,
        "expected_sha256": "abc",
    }


@respx.mock
def test_skills_edit_refuses_a_skill_you_did_not_write(tmp_path):
    respx.get(f"{API}/v1/skills/decision-record").respond(
        200, json={"id": "sk2", "slug": "decision-record", "owner": "system"}
    )
    put = respx.put(f"{API}/v1/skills/sk2/bundle").respond(200, json={})
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("x\n")
    result = runner.invoke(app, ["skills", "edit", "decision-record", "--file", str(skill_md)])
    assert result.exit_code == 2
    assert "not yours" in result.output
    assert not put.called


@respx.mock
def test_skills_proposals_and_approve(rendered):
    respx.get(f"{API}/v1/me/skills/proposals").respond(
        200, json=[{"id": "sp1", "title": "Weekly QC check", "occurrence_count": 4}]
    )
    accept = respx.post(f"{API}/v1/me/skills/proposals/sp1/accept").respond(200, json={"id": "sk9"})
    listed = runner.invoke(app, ["skills", "proposals"])
    assert listed.exit_code == 0, listed.output
    assert "Weekly QC check" in listed.output
    approved = runner.invoke(app, ["skills", "approve", "sp1", "--force"])
    assert approved.exit_code == 0, approved.output
    assert accept.calls.last.request.url.params["force"] == "true"


@respx.mock
def test_kb_undo_run_dry_run_reports_the_plan():
    route = respx.post(f"{API}/v1/knowledge/runs/run-7/undo").respond(
        200,
        json={
            "run": "run-7",
            "dry_run": True,
            "proposals": 2,
            "dismissed": ["p1"],
            "reverted": [{"proposal_id": "p2", "commit_id": "c2", "status": "planned"}],
            "conflicts": [],
            "unchanged": [],
            "truncated": False,
        },
    )
    result = runner.invoke(app, ["kb", "undo-run", "run-7", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {"dry_run": True}
    assert json.loads(result.output)["dismissed"] == ["p1"]


@respx.mock
def test_kb_conflicts_lists_contradicted_sections(rendered):
    respx.get(f"{API}/v1/knowledge/conflicts").respond(
        200,
        json={
            "data": [
                {
                    "page_id": "pg1",
                    "page_title": "HX-07",
                    "section_id": "sec_facts",
                    "contradicting": [{"name": "Later report"}],
                    "supporting": [],
                    "review_proposal_id": None,
                }
            ],
            "coverage": {},
            "next": [],
        },
    )
    result = runner.invoke(app, ["kb", "conflicts"])
    assert result.exit_code == 0, result.output
    assert "HX-07" in result.output and "Later report" in result.output
